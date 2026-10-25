import importlib.util
import json
from pathlib import Path

import pytest

from tradingbot.data.io import atomic_write_json, sha256_file
from tradingbot.data.ohlcv.manifest import write_ohlcv_manifests
from tradingbot.data.ohlcv.models import Candle
from tradingbot.data.ohlcv.quality import UnalignedTimestampError
from tradingbot.data.ohlcv.store import dump_candles, load_candles
from tradingbot.data.ohlcv.validation_4h import (
    ValidationDatasetError,
    inspect_4h_validation_dataset,
    rebuild_4h_manifest,
)

FOUR_H = 14_400_000
NOW = 3 * FOUR_H


def _c(ts: int, close: float = 1.5) -> Candle:
    return Candle(timestamp=ts, open=1, high=2, low=0.5, close=close, volume=10)


def _write_config(tmp_path: Path, timeframes: str = '["4h"]') -> Path:
    path = tmp_path / "ohlcv.toml"
    path.write_text(
        "\n".join(
            [
                'provider = "binance_public"',
                f"timeframes = {timeframes}",
                "candle_limit = 10",
                'raw_dir = "data/raw/ohlcv"',
                'processed_dir = "data/processed/ohlcv"',
                "retry_max = 0",
                "backoff_seconds = 0",
                "drop_incomplete = true",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return path


def _universe(tmp_path: Path, symbols: list[str], name: str = "universe_validation.json") -> Path:
    path = tmp_path / "data" / "processed" / name
    atomic_write_json(path, {"universe": [{"binance_symbol": item} for item in symbols]})
    return path


def _processed(tmp_path: Path) -> Path:
    return tmp_path / "data" / "processed" / "ohlcv"


def _dump(tmp_path: Path, symbol: str, candles: list[Candle]) -> Path:
    path = _processed(tmp_path) / symbol.replace("/", "") / "4h.json"
    dump_candles(path, candles, symbol=symbol, timeframe="4h")
    return path


def _manifest(tmp_path: Path, universe: Path) -> None:
    write_ohlcv_manifests(
        universe_path=universe,
        processed_root=_processed(tmp_path),
        timeframes=("4h",),
        provider="binance_public",
        candle_limit=10,
        drop_incomplete=True,
        now_ms=NOW,
        root=tmp_path,
    )


def _script(name: str):
    path = Path(__file__).resolve().parents[2] / "scripts" / name
    spec = importlib.util.spec_from_file_location(name.replace(".py", "_cli"), path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _Provider:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def fetch_ohlcv(self, symbol, timeframe, *, since=None, limit=None):
        self.calls.append(symbol)
        return [_c(0)]


def _ready(tmp_path: Path) -> tuple[Path, Path]:
    universe = _universe(tmp_path, ["BTC/USDT", "ETH/USDT"])
    btc = _dump(tmp_path, "BTC/USDT", [_c(0), _c(FOUR_H)])
    eth = _dump(tmp_path, "ETH/USDT", [_c(0), _c(FOUR_H)])
    _manifest(tmp_path, universe)
    return btc, eth


def test_ready_dataset_allows_validation_sync(tmp_path: Path, monkeypatch) -> None:
    _ready(tmp_path)
    provider = _Provider()
    module = _script("sync_ohlcv.py")
    monkeypatch.setattr(module, "BinancePublicOHLCVProvider", lambda: provider)
    code = module.main(
        [
            "--root",
            str(tmp_path),
            "--config",
            str(_write_config(tmp_path)),
            "--universe",
            "data/processed/universe_validation.json",
            "--require-4h-consistency",
        ]
    )
    assert code == 0
    assert provider.calls == ["BTC/USDT", "ETH/USDT"]


def test_hash_mismatch_stops_before_write(tmp_path: Path, monkeypatch) -> None:
    btc, eth = _ready(tmp_path)
    original = btc.read_bytes()
    btc.write_bytes(original + b" ")
    corrupted = btc.read_bytes()
    provider = _Provider()
    module = _script("sync_ohlcv.py")
    monkeypatch.setattr(module, "BinancePublicOHLCVProvider", lambda: provider)
    code = module.main(
        [
            "--root",
            str(tmp_path),
            "--config",
            str(_write_config(tmp_path)),
            "--universe",
            "data/processed/universe_validation.json",
            "--require-4h-consistency",
        ]
    )
    assert code == 1
    assert provider.calls == []
    assert btc.read_bytes() == corrupted
    assert eth.read_bytes() != b""


def test_missing_symbol_file_stops_validation_sync(tmp_path: Path, monkeypatch) -> None:
    universe = _universe(tmp_path, ["BTC/USDT", "ETH/USDT"])
    btc = _dump(tmp_path, "BTC/USDT", [_c(0), _c(FOUR_H)])
    _manifest(tmp_path, universe)
    before = btc.read_bytes()
    provider = _Provider()
    module = _script("sync_ohlcv.py")
    monkeypatch.setattr(module, "BinancePublicOHLCVProvider", lambda: provider)
    code = module.main(
        [
            "--root",
            str(tmp_path),
            "--config",
            str(_write_config(tmp_path)),
            "--universe",
            "data/processed/universe_validation.json",
            "--require-4h-consistency",
        ]
    )
    assert code == 1
    assert provider.calls == []
    assert btc.read_bytes() == before


def test_corrupt_symbol_file_stops_before_provider(tmp_path: Path, monkeypatch) -> None:
    btc, _eth = _ready(tmp_path)
    btc.write_bytes(b"{not json")
    before = btc.read_bytes()
    provider = _Provider()
    module = _script("sync_ohlcv.py")
    monkeypatch.setattr(module, "BinancePublicOHLCVProvider", lambda: provider)
    code = module.main(
        [
            "--root",
            str(tmp_path),
            "--config",
            str(_write_config(tmp_path)),
            "--universe",
            "data/processed/universe_validation.json",
            "--require-4h-consistency",
        ]
    )
    assert code == 1
    assert provider.calls == []
    assert btc.read_bytes() == before


def test_empty_dataset_still_fetches(tmp_path: Path, monkeypatch) -> None:
    _universe(tmp_path, ["BTC/USDT"])
    path = _processed(tmp_path) / "BTCUSDT" / "4h.json"
    assert not path.exists()
    provider = _Provider()
    module = _script("sync_ohlcv.py")
    monkeypatch.setattr(module, "BinancePublicOHLCVProvider", lambda: provider)
    code = module.main(
        [
            "--root",
            str(tmp_path),
            "--config",
            str(_write_config(tmp_path)),
            "--universe",
            "data/processed/universe_validation.json",
            "--require-4h-consistency",
        ]
    )
    assert code == 0
    assert provider.calls == ["BTC/USDT"]
    assert [item.timestamp for item in load_candles(path)] == [0]


def test_partial_files_without_manifest_are_not_initial(tmp_path: Path) -> None:
    universe = _universe(tmp_path, ["BTC/USDT", "ETH/USDT"])
    _dump(tmp_path, "BTC/USDT", [_c(0)])
    with pytest.raises(ValidationDatasetError, match="manifest_missing"):
        inspect_4h_validation_dataset(
            root=tmp_path,
            universe_path=universe,
            processed_root=_processed(tmp_path),
            now_ms=NOW,
            drop_incomplete=True,
        )


def test_rebuild_refreshes_stale_manifest_without_touching_candles(tmp_path: Path) -> None:
    btc, eth = _ready(tmp_path)
    dump_candles(btc, [_c(0), _c(FOUR_H), _c(2 * FOUR_H)], symbol="BTC/USDT", timeframe="4h")
    before = {btc: btc.read_bytes(), eth: eth.read_bytes()}
    manifest_path = _processed(tmp_path) / "manifest_4h.json"
    stale = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert stale["series"][0]["candle_count"] == 2
    written = rebuild_4h_manifest(
        root=tmp_path,
        universe_path=tmp_path / "data" / "processed" / "universe_validation.json",
        processed_root=_processed(tmp_path),
        provider="binance_public",
        candle_limit=10,
        drop_incomplete=True,
        now_ms=4 * FOUR_H,
        generated_at="2026-10-25T00:00:00+00:00",
    )
    assert written == manifest_path
    assert btc.read_bytes() == before[btc]
    assert eth.read_bytes() == before[eth]
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    universe = tmp_path / "data" / "processed" / "universe_validation.json"
    assert manifest["universe"]["path"] == "data/processed/universe_validation.json"
    assert manifest["universe"]["sha256"] == sha256_file(universe)
    assert manifest["universe"]["symbols"] == ["BTC/USDT", "ETH/USDT"]
    by_symbol = {row["symbol"]: row for row in manifest["series"]}
    assert by_symbol["BTC/USDT"]["candle_count"] == 3
    assert by_symbol["BTC/USDT"]["sha256"] == sha256_file(btc)
    assert by_symbol["BTC/USDT"]["range_end"] == 2 * FOUR_H


def test_rebuild_does_not_write_manifest_when_file_missing_or_corrupt(tmp_path: Path) -> None:
    universe = _universe(tmp_path, ["BTC/USDT", "ETH/USDT"])
    btc = _dump(tmp_path, "BTC/USDT", [_c(0), _c(FOUR_H)])
    _manifest(tmp_path, universe)
    manifest_path = _processed(tmp_path) / "manifest_4h.json"
    before_manifest = manifest_path.read_bytes()
    before_btc = btc.read_bytes()
    with pytest.raises(ValidationDatasetError, match="missing_file:ETH/USDT"):
        rebuild_4h_manifest(
            root=tmp_path,
            universe_path=universe,
            processed_root=_processed(tmp_path),
            provider="binance_public",
            candle_limit=10,
            drop_incomplete=True,
            now_ms=NOW,
        )
    assert manifest_path.read_bytes() == before_manifest
    assert btc.read_bytes() == before_btc

    eth = _dump(tmp_path, "ETH/USDT", [_c(0)])
    eth.write_bytes(b"{not json")
    corrupt = eth.read_bytes()
    with pytest.raises(ValidationDatasetError, match="unreadable_file:ETH/USDT"):
        rebuild_4h_manifest(
            root=tmp_path,
            universe_path=universe,
            processed_root=_processed(tmp_path),
            provider="binance_public",
            candle_limit=10,
            drop_incomplete=True,
            now_ms=NOW,
        )
    assert manifest_path.read_bytes() == before_manifest
    assert eth.read_bytes() == corrupt


def test_rebuild_script_rejects_corrupt_file(tmp_path: Path) -> None:
    universe = _universe(tmp_path, ["BTC/USDT"])
    path = _dump(tmp_path, "BTC/USDT", [_c(0)])
    path.write_bytes(b"{not json")
    module = _script("rebuild_4h_manifest.py")
    code = module.main(
        [
            "--root",
            str(tmp_path),
            "--config",
            str(_write_config(tmp_path)),
            "--universe",
            "data/processed/universe_validation.json",
        ]
    )
    assert code == 1
    assert not (_processed(tmp_path) / "manifest_4h.json").exists()
    assert universe.read_bytes()


def test_consistency_gate_rejects_unaligned_4h_before_provider(
    tmp_path: Path, monkeypatch
) -> None:
    _ready(tmp_path)
    btc = _processed(tmp_path) / "BTCUSDT" / "4h.json"
    dump_candles(
        btc,
        [Candle(timestamp=1_800_000, open=1, high=2, low=0.5, close=1.5, volume=10)],
        symbol="BTC/USDT",
        timeframe="4h",
    )
    before = btc.read_bytes()
    manifest_path = _processed(tmp_path) / "manifest_4h.json"
    before_manifest = manifest_path.read_bytes()
    provider = _Provider()
    module = _script("sync_ohlcv.py")
    monkeypatch.setattr(module, "BinancePublicOHLCVProvider", lambda: provider)
    code = module.main(
        [
            "--root",
            str(tmp_path),
            "--config",
            str(_write_config(tmp_path)),
            "--universe",
            "data/processed/universe_validation.json",
            "--require-4h-consistency",
        ]
    )
    assert code == 1
    assert provider.calls == []
    assert btc.read_bytes() == before
    assert manifest_path.read_bytes() == before_manifest


def test_rebuild_unaligned_4h_does_not_write_manifest(tmp_path: Path) -> None:
    universe = _universe(tmp_path, ["BTC/USDT"])
    path = _dump(tmp_path, "BTC/USDT", [_c(0), _c(FOUR_H)])
    _manifest(tmp_path, universe)
    manifest_path = _processed(tmp_path) / "manifest_4h.json"
    before_manifest = manifest_path.read_bytes()
    dump_candles(
        path,
        [Candle(timestamp=1_800_000, open=1, high=2, low=0.5, close=1.5, volume=10)],
        symbol="BTC/USDT",
        timeframe="4h",
    )
    before_candle = path.read_bytes()
    with pytest.raises(UnalignedTimestampError) as exc_info:
        rebuild_4h_manifest(
            root=tmp_path,
            universe_path=universe,
            processed_root=_processed(tmp_path),
            provider="binance_public",
            candle_limit=10,
            drop_incomplete=True,
            now_ms=NOW,
        )
    assert exc_info.value.symbol == "BTC/USDT"
    assert exc_info.value.timeframe == "4h"
    assert exc_info.value.timestamp == 1_800_000
    assert manifest_path.read_bytes() == before_manifest
    assert path.read_bytes() == before_candle

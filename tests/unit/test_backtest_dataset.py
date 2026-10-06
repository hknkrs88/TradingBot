from pathlib import Path

import pytest

from tradingbot.backtest.dataset import load_backtest_series
from tradingbot.backtest.engine import run_backtest
from tradingbot.backtest.models import BacktestConfig
from tradingbot.data.io import InvalidCacheError, atomic_write_json, sha256_file
from tradingbot.data.ohlcv.models import Candle
from tradingbot.data.ohlcv.store import dump_candles
from tradingbot.strategy.ma_crossover import MACrossover, MACrossoverParams

HOUR = 3_600_000
FOUR_H = 14_400_000
SCHEMA = "ohlcv_dataset_manifest/v1"
MANIFEST_REL = Path("data/processed/ohlcv/manifest_1h.json")


def _c(ts: int, close: float = 1.0) -> Candle:
    return Candle(timestamp=ts, open=close, high=close, low=close, close=close, volume=1)


def load_payload(path: Path) -> dict:
    import json

    return json.loads(path.read_text(encoding="utf-8"))


def _write_universe(root: Path, symbols: list[str]) -> Path:
    path = root / "data" / "processed" / "universe.json"
    atomic_write_json(path, {"universe": [{"binance_symbol": item} for item in symbols]})
    return path


def _write_processed(
    root: Path,
    symbol: str,
    timeframe: str,
    candles: list[Candle],
) -> Path:
    safe = symbol.replace("/", "")
    path = root / "data" / "processed" / "ohlcv" / safe / f"{timeframe}.json"
    dump_candles(path, candles, symbol=symbol, timeframe=timeframe)
    return path


def _manifest(
    *,
    timeframe: str,
    symbols: list[str],
    universe_sha: str,
    series: list[dict],
    incomplete: list[str] | None = None,
    schema: str = SCHEMA,
) -> dict:
    return {
        "schema": schema,
        "generated_at": "2026-10-06T17:00:00+00:00",
        "provider": "binance_public",
        "timeframe": timeframe,
        "universe": {
            "path": "data/processed/universe.json",
            "sha256": universe_sha,
            "symbol_count": len(symbols),
            "symbols": list(symbols),
        },
        "config": {"provider": "binance_public", "candle_limit": 1000, "drop_incomplete": True},
        "series": series,
        "incomplete_symbols": list(incomplete or []),
    }


def _series_row(
    processed: Path,
    *,
    symbol: str = "BTC/USDT",
    timeframe: str = "1h",
    candles: list[Candle] | None = None,
    **overrides: object,
) -> dict:
    rows = candles if candles is not None else [_c(0), _c(HOUR), _c(2 * HOUR)]
    payload = {
        "symbol": symbol,
        "path": f"{symbol.replace('/', '')}/{timeframe}.json",
        "range_start": rows[0].timestamp if rows else 0,
        "range_end": rows[-1].timestamp if rows else 0,
        "candle_count": len(rows),
        "sha256": sha256_file(processed),
    }
    payload.update(overrides)
    return payload


def _ready(
    tmp_path: Path,
    *,
    candles: list[Candle] | None = None,
    symbol: str = "BTC/USDT",
    timeframe: str = "1h",
    universe_symbols: list[str] | None = None,
    incomplete: list[str] | None = None,
) -> Path:
    tf_step = HOUR if timeframe == "1h" else FOUR_H
    rows = candles or [_c(0), _c(tf_step), _c(2 * tf_step)]
    names = universe_symbols if universe_symbols is not None else [symbol]
    universe = _write_universe(tmp_path, names)
    processed = _write_processed(tmp_path, symbol, timeframe, rows)
    manifest = tmp_path / "data" / "processed" / "ohlcv" / f"manifest_{timeframe}.json"
    atomic_write_json(
        manifest,
        _manifest(
            timeframe=timeframe,
            symbols=names,
            universe_sha=sha256_file(universe),
            series=[_series_row(processed, symbol=symbol, timeframe=timeframe, candles=rows)],
            incomplete=incomplete,
        ),
    )
    return manifest


def test_valid_manifest_loads_dataset(tmp_path: Path) -> None:
    candles = [_c(0, 10), _c(HOUR, 11), _c(2 * HOUR, 12)]
    _ready(tmp_path, candles=candles)
    processed = tmp_path / "data" / "processed" / "ohlcv" / "BTCUSDT" / "1h.json"
    dataset = load_backtest_series(MANIFEST_REL, "BTC/USDT", root=tmp_path)
    assert dataset.timeframe == "1h"
    assert dataset.bar_count == 3
    assert dataset.processed_relpath == "BTCUSDT/1h.json"
    assert dataset.processed_sha256 == sha256_file(processed)
    assert dataset.universe_sha256 == sha256_file(tmp_path / "data" / "processed" / "universe.json")
    assert dataset.manifest_path == MANIFEST_REL.as_posix()
    assert [c.timestamp for c in dataset.series.candles] == [0, HOUR, 2 * HOUR]


def test_missing_manifest_fails(tmp_path: Path) -> None:
    with pytest.raises(InvalidCacheError):
        load_backtest_series(MANIFEST_REL, "BTC/USDT", root=tmp_path)


def test_invalid_json_fails(tmp_path: Path) -> None:
    path = tmp_path / MANIFEST_REL
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not-json", encoding="utf-8")
    with pytest.raises(InvalidCacheError):
        load_backtest_series(MANIFEST_REL, "BTC/USDT", root=tmp_path)


def test_invalid_schema_fails(tmp_path: Path) -> None:
    manifest = _ready(tmp_path)
    payload = load_payload(manifest)
    payload["schema"] = "other/v0"
    atomic_write_json(manifest, payload)
    with pytest.raises(ValueError, match="schema"):
        load_backtest_series(MANIFEST_REL, "BTC/USDT", root=tmp_path)


def test_missing_timeframe_fails(tmp_path: Path) -> None:
    manifest = _ready(tmp_path)
    payload = load_payload(manifest)
    del payload["timeframe"]
    atomic_write_json(manifest, payload)
    with pytest.raises(ValueError, match="timeframe"):
        load_backtest_series(MANIFEST_REL, "BTC/USDT", root=tmp_path)


def test_invalid_timeframe_fails(tmp_path: Path) -> None:
    manifest = _ready(tmp_path)
    payload = load_payload(manifest)
    payload["timeframe"] = "15m"
    atomic_write_json(manifest, payload)
    with pytest.raises(ValueError):
        load_backtest_series(MANIFEST_REL, "BTC/USDT", root=tmp_path)


def test_filename_timeframe_mismatch_fails(tmp_path: Path) -> None:
    manifest = _ready(tmp_path)
    payload = load_payload(manifest)
    payload["timeframe"] = "4h"
    atomic_write_json(manifest, payload)
    with pytest.raises(ValueError, match="filename"):
        load_backtest_series(MANIFEST_REL, "BTC/USDT", root=tmp_path)


def test_symbol_not_in_universe_fails(tmp_path: Path) -> None:
    _ready(tmp_path, symbol="ETH/USDT", universe_symbols=["ETH/USDT"])
    with pytest.raises(ValueError, match="not in universe"):
        load_backtest_series(MANIFEST_REL, "BTC/USDT", root=tmp_path)


def test_incomplete_symbol_fails(tmp_path: Path) -> None:
    _ready(tmp_path, incomplete=["BTC/USDT"])
    with pytest.raises(ValueError, match="incomplete"):
        load_backtest_series(MANIFEST_REL, "BTC/USDT", root=tmp_path)


def test_missing_series_record_fails(tmp_path: Path) -> None:
    manifest = _ready(tmp_path, universe_symbols=["BTC/USDT", "ETH/USDT"])
    payload = load_payload(manifest)
    payload["series"] = []
    atomic_write_json(manifest, payload)
    with pytest.raises(ValueError, match="no series"):
        load_backtest_series(MANIFEST_REL, "BTC/USDT", root=tmp_path)


def test_duplicate_series_record_fails(tmp_path: Path) -> None:
    manifest = _ready(tmp_path)
    payload = load_payload(manifest)
    payload["series"] = [payload["series"][0], dict(payload["series"][0])]
    atomic_write_json(manifest, payload)
    with pytest.raises(ValueError, match="duplicate"):
        load_backtest_series(MANIFEST_REL, "BTC/USDT", root=tmp_path)


def test_universe_missing_fails(tmp_path: Path) -> None:
    _ready(tmp_path)
    (tmp_path / "data" / "processed" / "universe.json").unlink()
    with pytest.raises(InvalidCacheError, match="universe"):
        load_backtest_series(MANIFEST_REL, "BTC/USDT", root=tmp_path)


def test_universe_hash_mismatch_fails(tmp_path: Path) -> None:
    manifest = _ready(tmp_path)
    payload = load_payload(manifest)
    payload["universe"]["sha256"] = "0" * 64
    atomic_write_json(manifest, payload)
    with pytest.raises(ValueError, match="universe hash"):
        load_backtest_series(MANIFEST_REL, "BTC/USDT", root=tmp_path)


def test_processed_missing_fails(tmp_path: Path) -> None:
    _ready(tmp_path)
    (tmp_path / "data" / "processed" / "ohlcv" / "BTCUSDT" / "1h.json").unlink()
    with pytest.raises(InvalidCacheError, match="processed"):
        load_backtest_series(MANIFEST_REL, "BTC/USDT", root=tmp_path)


def test_processed_hash_mismatch_fails(tmp_path: Path) -> None:
    manifest = _ready(tmp_path)
    payload = load_payload(manifest)
    payload["series"][0]["sha256"] = "0" * 64
    atomic_write_json(manifest, payload)
    with pytest.raises(ValueError, match="processed hash"):
        load_backtest_series(MANIFEST_REL, "BTC/USDT", root=tmp_path)


def test_processed_path_outside_root_fails(tmp_path: Path) -> None:
    manifest = _ready(tmp_path)
    payload = load_payload(manifest)
    payload["series"][0]["path"] = "../universe.json"
    atomic_write_json(manifest, payload)
    with pytest.raises(ValueError, match="escapes"):
        load_backtest_series(MANIFEST_REL, "BTC/USDT", root=tmp_path)


def test_processed_path_absolute_fails(tmp_path: Path) -> None:
    manifest = _ready(tmp_path)
    processed = tmp_path / "data" / "processed" / "ohlcv" / "BTCUSDT" / "1h.json"
    payload = load_payload(manifest)
    payload["series"][0]["path"] = str(processed.resolve())
    atomic_write_json(manifest, payload)
    with pytest.raises(ValueError, match="relative"):
        load_backtest_series(MANIFEST_REL, "BTC/USDT", root=tmp_path)


def test_empty_processed_fails(tmp_path: Path) -> None:
    universe = _write_universe(tmp_path, ["BTC/USDT"])
    processed = _write_processed(tmp_path, "BTC/USDT", "1h", [])
    manifest = tmp_path / MANIFEST_REL
    atomic_write_json(
        manifest,
        _manifest(
            timeframe="1h",
            symbols=["BTC/USDT"],
            universe_sha=sha256_file(universe),
            series=[
                {
                    "symbol": "BTC/USDT",
                    "path": "BTCUSDT/1h.json",
                    "range_start": 0,
                    "range_end": 0,
                    "candle_count": 0,
                    "sha256": sha256_file(processed),
                }
            ],
        ),
    )
    with pytest.raises(ValueError, match="empty"):
        load_backtest_series(MANIFEST_REL, "BTC/USDT", root=tmp_path)


def test_invalid_candleseries_fails(tmp_path: Path) -> None:
    candles = [_c(HOUR), _c(0), _c(2 * HOUR)]
    universe = _write_universe(tmp_path, ["BTC/USDT"])
    processed = _write_processed(tmp_path, "BTC/USDT", "1h", candles)
    manifest = tmp_path / MANIFEST_REL
    atomic_write_json(
        manifest,
        _manifest(
            timeframe="1h",
            symbols=["BTC/USDT"],
            universe_sha=sha256_file(universe),
            series=[_series_row(processed, candles=candles)],
        ),
    )
    with pytest.raises(ValueError, match="sorted"):
        load_backtest_series(MANIFEST_REL, "BTC/USDT", root=tmp_path)


def test_range_start_mismatch_fails(tmp_path: Path) -> None:
    manifest = _ready(tmp_path)
    payload = load_payload(manifest)
    payload["series"][0]["range_start"] = 99
    atomic_write_json(manifest, payload)
    with pytest.raises(ValueError, match="range_start"):
        load_backtest_series(MANIFEST_REL, "BTC/USDT", root=tmp_path)


def test_range_end_mismatch_fails(tmp_path: Path) -> None:
    manifest = _ready(tmp_path)
    payload = load_payload(manifest)
    payload["series"][0]["range_end"] = 99
    atomic_write_json(manifest, payload)
    with pytest.raises(ValueError, match="range_end"):
        load_backtest_series(MANIFEST_REL, "BTC/USDT", root=tmp_path)


def test_candle_count_mismatch_fails(tmp_path: Path) -> None:
    manifest = _ready(tmp_path)
    payload = load_payload(manifest)
    payload["series"][0]["candle_count"] = 99
    atomic_write_json(manifest, payload)
    with pytest.raises(ValueError, match="candle_count"):
        load_backtest_series(MANIFEST_REL, "BTC/USDT", root=tmp_path)


def test_valid_metadata_fields(tmp_path: Path) -> None:
    candles = [_c(0), _c(HOUR)]
    _ready(tmp_path, candles=candles)
    processed = tmp_path / "data" / "processed" / "ohlcv" / "BTCUSDT" / "1h.json"
    dataset = load_backtest_series(MANIFEST_REL, "BTC/USDT", root=tmp_path)
    assert dataset.bar_count == 2
    assert dataset.timeframe == "1h"
    assert dataset.processed_sha256 == sha256_file(processed)
    assert dataset.series.symbol == "BTC/USDT"


def test_deterministic_reload(tmp_path: Path) -> None:
    _ready(tmp_path)
    first = load_backtest_series(MANIFEST_REL, "BTC/USDT", root=tmp_path)
    second = load_backtest_series(MANIFEST_REL, "BTC/USDT", root=tmp_path)
    assert first.series == second.series
    assert first == second


def test_1h_and_4h_manifests(tmp_path: Path) -> None:
    _ready(tmp_path, timeframe="1h", candles=[_c(0), _c(HOUR)])
    four = [_c(0), _c(FOUR_H), _c(2 * FOUR_H)]
    universe = tmp_path / "data" / "processed" / "universe.json"
    processed_4h = _write_processed(tmp_path, "BTC/USDT", "4h", four)
    atomic_write_json(
        tmp_path / "data" / "processed" / "ohlcv" / "manifest_4h.json",
        _manifest(
            timeframe="4h",
            symbols=["BTC/USDT"],
            universe_sha=sha256_file(universe),
            series=[_series_row(processed_4h, timeframe="4h", candles=four)],
        ),
    )
    one = load_backtest_series(MANIFEST_REL, "BTC/USDT", root=tmp_path)
    four_ds = load_backtest_series(
        Path("data/processed/ohlcv/manifest_4h.json"), "BTC/USDT", root=tmp_path
    )
    assert one.timeframe == "1h"
    assert four_ds.timeframe == "4h"
    assert one.processed_relpath == "BTCUSDT/1h.json"
    assert four_ds.processed_relpath == "BTCUSDT/4h.json"
    assert one.bar_count == 2
    assert four_ds.bar_count == 3


def test_full_pipeline_tmp_dataset(tmp_path: Path) -> None:
    candles = [
        _c(0, 1.0),
        _c(HOUR, 1.0),
        _c(2 * HOUR, 1.0),
        _c(3 * HOUR, 10.0),
        _c(4 * HOUR, 10.0),
        _c(5 * HOUR, 1.0),
        _c(6 * HOUR, 1.0),
    ]
    _ready(tmp_path, candles=candles)
    dataset = load_backtest_series(MANIFEST_REL, "BTC/USDT", root=tmp_path)
    strategy = MACrossover(MACrossoverParams(fast=2, slow=3))
    signals = strategy.generate(dataset.series)
    config = BacktestConfig(initial_cash=10_000.0, commission_rate=0.001)
    first = run_backtest(dataset.series, signals, config)
    second = run_backtest(dataset.series, signals, config)
    assert first == second
    assert len(first.equity) == dataset.bar_count
    assert first.metadata["symbol"] == "BTC/USDT"


def test_dataset_module_does_not_import_strategy_impl() -> None:
    text = (
        Path(__file__).resolve().parents[2] / "src" / "tradingbot" / "backtest" / "dataset.py"
    ).read_text(encoding="utf-8")
    assert "ma_crossover" not in text
    assert "run_backtest" not in text
    assert "ccxt" not in text
    assert "tradingbot.broker" not in text

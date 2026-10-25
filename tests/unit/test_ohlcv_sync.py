import importlib.util
import json
from dataclasses import replace
from pathlib import Path

import ccxt
import pytest

from tradingbot.data.io import atomic_write_json, sha256_file
from tradingbot.data.ohlcv.models import Candle
from tradingbot.data.ohlcv.quality import UnalignedTimestampError
from tradingbot.data.ohlcv.store import dump_candles, load_candles
from tradingbot.data.ohlcv.sync import OHLCVConfig, sync_ohlcv, sync_symbol_timeframe

HOUR = 3_600_000
NOW = 4 * HOUR + 100
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "ohlcv"


def _c(ts: int) -> Candle:
    return Candle(timestamp=ts, open=1, high=2, low=0.5, close=1.5, volume=10)


def _config(tmp_path: Path, timeframes: tuple[str, ...] = ("1h",)) -> OHLCVConfig:
    return OHLCVConfig(
        provider="binance_public",
        timeframes=timeframes,
        candle_limit=100,
        raw_dir="raw",
        processed_dir="processed",
        retry_max=2,
        backoff_seconds=0.01,
        drop_incomplete=True,
    )


class FakeProvider:
    def __init__(self, responses: dict[tuple[str, str], list[Candle] | Exception]) -> None:
        self.responses = responses
        self.calls: list[dict] = []

    def fetch_ohlcv(self, symbol, timeframe, *, since=None, limit=None):
        self.calls.append(
            {"symbol": symbol, "timeframe": timeframe, "since": since, "limit": limit}
        )
        value = self.responses[(symbol, timeframe)]
        if isinstance(value, Exception):
            raise value
        return value


class FlakyProvider:
    def __init__(self, fail_times: int, candles: list[Candle]) -> None:
        self.fail_times = fail_times
        self.candles = candles
        self.calls = 0
        self.sleeps: list[float] = []

    def fetch_ohlcv(self, symbol, timeframe, *, since=None, limit=None):
        self.calls += 1
        if self.calls <= self.fail_times:
            raise ccxt.RateLimitExceeded("wait")
        return self.candles


def test_cache_hit_does_not_call_provider(tmp_path: Path) -> None:
    universe = FIXTURES / "universe.json"
    processed = tmp_path / "processed" / "BTCUSDT" / "1h.json"
    dump_candles(
        processed,
        [_c(0), _c(HOUR), _c(2 * HOUR), _c(3 * HOUR)],
        symbol="BTC/USDT",
        timeframe="1h",
    )
    dump_candles(
        tmp_path / "processed" / "ETHUSDT" / "1h.json",
        [_c(0), _c(HOUR), _c(2 * HOUR), _c(3 * HOUR)],
        symbol="ETH/USDT",
        timeframe="1h",
    )
    provider = FakeProvider({})
    report = sync_ohlcv(
        universe_path=universe,
        config=_config(tmp_path),
        provider=provider,
        root=tmp_path,
        now_ms=NOW,
        sleep_fn=lambda _d: None,
    )
    assert provider.calls == []
    assert report["cache_hits"] == 2
    assert report["cache_misses"] == 0
    assert report["candles_fetched"] == 0
    assert report["symbols_ok"] == 2


def test_incremental_since_is_last_closed_plus_timeframe(tmp_path: Path) -> None:
    universe = tmp_path / "universe.json"
    atomic_write_json(
        universe,
        {"universe": [{"binance_symbol": "BTC/USDT"}]},
    )
    dump_candles(
        tmp_path / "processed" / "BTCUSDT" / "1h.json",
        [_c(0)],
        symbol="BTC/USDT",
        timeframe="1h",
    )
    provider = FakeProvider({("BTC/USDT", "1h"): [_c(HOUR), _c(2 * HOUR)]})
    now = 4 * HOUR
    report = sync_ohlcv(
        universe_path=universe,
        config=_config(tmp_path),
        provider=provider,
        root=tmp_path,
        now_ms=now,
        sleep_fn=lambda _d: None,
    )
    assert provider.calls[0]["since"] == HOUR
    assert report["cache_misses"] == 1
    assert report["candles_fetched"] == 2
    assert report["series"][0]["reason"] == "fetched"


def test_sync_retry_then_success(tmp_path: Path) -> None:
    universe = tmp_path / "universe.json"
    atomic_write_json(universe, {"universe": [{"binance_symbol": "BTC/USDT"}]})
    provider = FlakyProvider(fail_times=2, candles=[_c(0), _c(HOUR)])
    report = sync_ohlcv(
        universe_path=universe,
        config=_config(tmp_path),
        provider=provider,
        root=tmp_path,
        now_ms=4 * HOUR,
        sleep_fn=lambda _d: None,
    )
    assert provider.calls == 3
    assert report["symbols_ok"] == 1
    assert report["symbols_failed"] == 0


def test_retry_exhausted_continues_other_symbols(tmp_path: Path) -> None:
    universe = tmp_path / "universe.json"
    atomic_write_json(
        universe,
        {
            "universe": [
                {"binance_symbol": "ETH/USDT"},
                {"binance_symbol": "BTC/USDT"},
            ]
        },
    )
    provider = FakeProvider(
        {
            ("ETH/USDT", "1h"): ccxt.NetworkError("nope"),
            ("BTC/USDT", "1h"): [_c(0), _c(HOUR)],
        }
    )
    report = sync_ohlcv(
        universe_path=universe,
        config=_config(tmp_path),
        provider=provider,
        root=tmp_path,
        now_ms=4 * HOUR,
        sleep_fn=lambda _d: None,
    )
    assert report["symbols_ok"] == 1
    assert report["symbols_failed"] == 1
    assert report["errors"][0]["symbol"] == "ETH/USDT"
    assert (tmp_path / "processed" / "sync_report.json").is_file()


def test_sync_rejects_15m_without_provider_call(tmp_path: Path) -> None:
    universe = tmp_path / "universe.json"
    atomic_write_json(universe, {"universe": [{"binance_symbol": "BTC/USDT"}]})
    provider = FakeProvider({})
    config = _config(tmp_path, timeframes=("15m",))
    report = sync_ohlcv(
        universe_path=universe,
        config=config,
        provider=provider,
        root=tmp_path,
        now_ms=NOW,
        sleep_fn=lambda _d: None,
    )
    assert provider.calls == []
    assert report["symbols_failed"] == 1
    assert report["errors"][0]["reason"] == "unsupported_timeframe"


def test_quality_issues_appear_in_sync_report_series(tmp_path: Path) -> None:
    universe = tmp_path / "universe.json"
    atomic_write_json(universe, {"universe": [{"binance_symbol": "BTC/USDT"}]})
    provider = FakeProvider({("BTC/USDT", "1h"): [_c(0), _c(2 * HOUR)]})
    report = sync_ohlcv(
        universe_path=universe,
        config=_config(tmp_path),
        provider=provider,
        root=tmp_path,
        now_ms=4 * HOUR,
        sleep_fn=lambda _d: None,
    )
    series = report["series"][0]
    assert series["reason"] == "fetched"
    assert any(item["reason"] == "missing_candles" for item in series["issues"])
    saved = json.loads((tmp_path / "processed" / "sync_report.json").read_text(encoding="utf-8"))
    assert saved["series"][0]["issues"] == series["issues"]


def test_merge_cleans_dirty_processed_cache(tmp_path: Path) -> None:
    universe = tmp_path / "universe.json"
    atomic_write_json(universe, {"universe": [{"binance_symbol": "BTC/USDT"}]})
    dirty = [
        _c(0),
        Candle(timestamp=HOUR, open=1.0, high=1.0, low=0.5, close=1.5, volume=10.0),
        _c(4 * HOUR),
    ]
    dump_candles(
        tmp_path / "processed" / "BTCUSDT" / "1h.json",
        dirty,
        symbol="BTC/USDT",
        timeframe="1h",
    )
    provider = FakeProvider({("BTC/USDT", "1h"): [_c(2 * HOUR)]})
    now = 4 * HOUR
    report = sync_ohlcv(
        universe_path=universe,
        config=_config(tmp_path),
        provider=provider,
        root=tmp_path,
        now_ms=now,
        sleep_fn=lambda _d: None,
    )
    cleaned = load_candles(tmp_path / "processed" / "BTCUSDT" / "1h.json")
    assert [c.timestamp for c in cleaned] == [0, 2 * HOUR]
    reasons = {item["reason"] for item in report["series"][0]["issues"]}
    assert "invalid_ohlc" in reasons
    assert "incomplete_candle" in reasons
    assert report["series"][0]["reason"] == "fetched"


def _btc_universe(tmp_path: Path) -> Path:
    universe = tmp_path / "universe.json"
    atomic_write_json(universe, {"universe": [{"binance_symbol": "BTC/USDT"}]})
    return universe


def test_cache_hit_sanitizes_dirty_processed_without_provider(tmp_path: Path) -> None:
    universe = _btc_universe(tmp_path)
    dirty = [
        _c(0),
        _c(HOUR),
        _c(2 * HOUR),
        _c(3 * HOUR),
        Candle(timestamp=1_800_000, open=1.0, high=1.0, low=0.5, close=1.5, volume=10.0),
        Candle(timestamp=1_800_001, open=1.0, high=2.0, low=0.5, close=1.5, volume=-1.0),
        _c(4 * HOUR),
    ]
    path = tmp_path / "processed" / "BTCUSDT" / "1h.json"
    dump_candles(path, dirty, symbol="BTC/USDT", timeframe="1h")
    provider = FakeProvider({})
    report = sync_ohlcv(
        universe_path=universe,
        config=_config(tmp_path),
        provider=provider,
        root=tmp_path,
        now_ms=NOW,
        sleep_fn=lambda _d: None,
    )
    assert provider.calls == []
    assert report["series"][0]["reason"] == "cache_hit"
    cleaned = load_candles(path)
    assert [c.timestamp for c in cleaned] == [0, HOUR, 2 * HOUR, 3 * HOUR]
    reasons = {item["reason"] for item in report["series"][0]["issues"]}
    assert "invalid_ohlc" in reasons
    assert "negative_volume" in reasons
    assert "incomplete_candle" in reasons


def test_valid_cache_hit_skips_provider_and_keeps_candles(tmp_path: Path) -> None:
    universe = _btc_universe(tmp_path)
    valid = [_c(0), _c(HOUR), _c(2 * HOUR), _c(3 * HOUR)]
    path = tmp_path / "processed" / "BTCUSDT" / "1h.json"
    dump_candles(path, valid, symbol="BTC/USDT", timeframe="1h")
    before = path.read_text(encoding="utf-8")
    provider = FakeProvider({})
    report = sync_ohlcv(
        universe_path=universe,
        config=_config(tmp_path),
        provider=provider,
        root=tmp_path,
        now_ms=NOW,
        sleep_fn=lambda _d: None,
    )
    assert provider.calls == []
    assert report["series"][0]["reason"] == "cache_hit"
    assert path.read_text(encoding="utf-8") == before
    assert [c.timestamp for c in load_candles(path)] == [0, HOUR, 2 * HOUR, 3 * HOUR]


def test_cache_hit_deduplicates_timestamps(tmp_path: Path) -> None:
    universe = _btc_universe(tmp_path)
    path = tmp_path / "processed" / "BTCUSDT" / "1h.json"
    dump_candles(
        path,
        [_c(0), _c(0), _c(HOUR), _c(2 * HOUR), _c(3 * HOUR)],
        symbol="BTC/USDT",
        timeframe="1h",
    )
    provider = FakeProvider({})
    report = sync_ohlcv(
        universe_path=universe,
        config=_config(tmp_path),
        provider=provider,
        root=tmp_path,
        now_ms=NOW,
        sleep_fn=lambda _d: None,
    )
    assert provider.calls == []
    assert report["series"][0]["reason"] == "cache_hit"
    stamps = [c.timestamp for c in load_candles(path)]
    assert stamps == sorted(stamps)
    assert len(stamps) == len(set(stamps))
    assert any(item["reason"] == "duplicate_timestamp" for item in report["series"][0]["issues"])


FOUR_H = 14_400_000


def test_corrupt_4h_cache_stops_without_provider_or_overwrite(tmp_path: Path) -> None:
    universe = _btc_universe(tmp_path)
    path = tmp_path / "processed" / "BTCUSDT" / "4h.json"
    dump_candles(path, [_c(0), _c(FOUR_H)], symbol="BTC/USDT", timeframe="4h")
    path.write_bytes(b"{not json")
    before = path.read_bytes()
    provider = FakeProvider({("BTC/USDT", "4h"): [_c(2 * FOUR_H)]})
    report = sync_ohlcv(
        universe_path=universe,
        config=_config(tmp_path, timeframes=("4h",)),
        provider=provider,
        root=tmp_path,
        now_ms=4 * FOUR_H,
        sleep_fn=lambda _d: None,
    )
    assert provider.calls == []
    assert path.read_bytes() == before
    assert not (tmp_path / "raw" / "binance_public" / "BTCUSDT" / "4h.json").exists()
    assert report["symbols_failed"] == 1
    assert report["symbols_ok"] == 0
    assert report["errors"][0]["symbol"] == "BTC/USDT"
    assert report["errors"][0]["timeframe"] == "4h"
    assert report["errors"][0]["reason"] == "InvalidCacheError"
    assert report["series"][0]["reason"] == "failed"


def test_missing_processed_file_still_fetches(tmp_path: Path) -> None:
    universe = _btc_universe(tmp_path)
    path = tmp_path / "processed" / "BTCUSDT" / "4h.json"
    assert not path.exists()
    provider = FakeProvider({("BTC/USDT", "4h"): [_c(0)]})
    report = sync_ohlcv(
        universe_path=universe,
        config=_config(tmp_path, timeframes=("4h",)),
        provider=provider,
        root=tmp_path,
        now_ms=2 * FOUR_H,
        sleep_fn=lambda _d: None,
    )
    assert provider.calls[0]["since"] is None
    assert report["symbols_ok"] == 1
    assert report["symbols_failed"] == 0
    assert [c.timestamp for c in load_candles(path)] == [0]


def _sync_script():
    path = Path(__file__).resolve().parents[2] / "scripts" / "sync_ohlcv.py"
    spec = importlib.util.spec_from_file_location("sync_ohlcv_cli", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write_ohlcv_config(tmp_path: Path) -> Path:
    path = tmp_path / "ohlcv.toml"
    path.write_text(
        "\n".join(
            [
                'provider = "binance_public"',
                'timeframes = ["1h"]',
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


class _CliProvider:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def fetch_ohlcv(self, symbol, timeframe, *, since=None, limit=None):
        self.calls.append(symbol)
        return [_c(0)]


def test_cli_without_universe_uses_default_file(tmp_path: Path, monkeypatch) -> None:
    live = tmp_path / "data" / "processed" / "universe.json"
    atomic_write_json(live, {"universe": [{"binance_symbol": "BTC/USDT"}]})
    other = tmp_path / "data" / "processed" / "universe_validation.json"
    atomic_write_json(other, {"universe": [{"binance_symbol": "ETH/USDT"}]})
    config = _write_ohlcv_config(tmp_path)
    provider = _CliProvider()
    module = _sync_script()
    monkeypatch.setattr(module, "BinancePublicOHLCVProvider", lambda: provider)
    code = module.main(["--root", str(tmp_path), "--config", str(config)])
    assert code == 0
    assert provider.calls == ["BTC/USDT"]
    manifest = json.loads(
        (tmp_path / "data" / "processed" / "ohlcv" / "manifest_1h.json").read_text(encoding="utf-8")
    )
    assert manifest["universe"]["path"] == "data/processed/universe.json"
    assert manifest["universe"]["sha256"] == sha256_file(live)


def test_cli_universe_flag_syncs_only_that_file(tmp_path: Path, monkeypatch) -> None:
    live = tmp_path / "data" / "processed" / "universe.json"
    atomic_write_json(
        live,
        {"universe": [{"binance_symbol": "BTC/USDT"}, {"binance_symbol": "ETH/USDT"}]},
    )
    before = live.read_bytes()
    validation = tmp_path / "data" / "processed" / "universe_validation.json"
    atomic_write_json(validation, {"universe": [{"binance_symbol": "ETH/USDT"}]})
    config = _write_ohlcv_config(tmp_path)
    provider = _CliProvider()
    module = _sync_script()
    monkeypatch.setattr(module, "BinancePublicOHLCVProvider", lambda: provider)
    code = module.main(
        [
            "--root",
            str(tmp_path),
            "--config",
            str(config),
            "--universe",
            "data/processed/universe_validation.json",
        ]
    )
    assert code == 0
    assert provider.calls == ["ETH/USDT"]
    assert live.read_bytes() == before
    manifest = json.loads(
        (tmp_path / "data" / "processed" / "ohlcv" / "manifest_1h.json").read_text(encoding="utf-8")
    )
    assert manifest["universe"]["path"] == "data/processed/universe_validation.json"
    assert manifest["universe"]["sha256"] == sha256_file(validation)
    assert manifest["universe"]["symbols"] == ["ETH/USDT"]


def test_4h_open_candle_not_written_exact_close_is(tmp_path: Path) -> None:
    universe = _btc_universe(tmp_path)
    now = 2 * FOUR_H
    boundary = Candle(timestamp=FOUR_H, open=1, high=2, low=0.5, close=1.5, volume=10)
    still_open = Candle(timestamp=2 * FOUR_H, open=4, high=5, low=3, close=4.5, volume=12)
    provider = FakeProvider({("BTC/USDT", "4h"): [_c(0), boundary, still_open]})
    report = sync_ohlcv(
        universe_path=universe,
        config=_config(tmp_path, timeframes=("4h",)),
        provider=provider,
        root=tmp_path,
        now_ms=now,
        sleep_fn=lambda _d: None,
    )
    saved = load_candles(tmp_path / "processed" / "BTCUSDT" / "4h.json")
    assert saved == [_c(0), boundary]
    assert any(
        item["reason"] == "incomplete_candle" and item["timestamp"] == still_open.timestamp
        for item in report["series"][0]["issues"]
    )


def test_4h_conflicting_ohlc_does_not_overwrite_existing(tmp_path: Path) -> None:
    universe = _btc_universe(tmp_path)
    existing = Candle(timestamp=0, open=1, high=2, low=0.5, close=1.5, volume=10)
    path = tmp_path / "processed" / "BTCUSDT" / "4h.json"
    dump_candles(path, [existing], symbol="BTC/USDT", timeframe="4h")
    incoming = Candle(timestamp=0, open=10, high=20, low=5, close=15, volume=99)
    added = Candle(timestamp=FOUR_H, open=3, high=4, low=2, close=3.5, volume=8)
    report = sync_ohlcv(
        universe_path=universe,
        config=_config(tmp_path, timeframes=("4h",)),
        provider=FakeProvider({("BTC/USDT", "4h"): [incoming, added]}),
        root=tmp_path,
        now_ms=3 * FOUR_H,
        sleep_fn=lambda _d: None,
    )
    assert load_candles(path) == [existing, added]
    assert {"reason": "duplicate_timestamp", "timestamp": 0} in report["series"][0]["issues"]


def test_4h_missing_candles_reported_not_filled(tmp_path: Path) -> None:
    universe = _btc_universe(tmp_path)
    report = sync_ohlcv(
        universe_path=universe,
        config=_config(tmp_path, timeframes=("4h",)),
        provider=FakeProvider({("BTC/USDT", "4h"): [_c(0), _c(2 * FOUR_H)]}),
        root=tmp_path,
        now_ms=4 * FOUR_H,
        sleep_fn=lambda _d: None,
    )
    saved = load_candles(tmp_path / "processed" / "BTCUSDT" / "4h.json")
    assert [c.timestamp for c in saved] == [0, 2 * FOUR_H]
    assert report["symbols_ok"] == 1
    missing = [
        item for item in report["series"][0]["issues"] if item["reason"] == "missing_candles"
    ]
    assert missing == [{"reason": "missing_candles", "timestamp": FOUR_H}]


def test_4h_duplicate_timestamp_in_file_drops_both(tmp_path: Path) -> None:
    universe = _btc_universe(tmp_path)
    first = Candle(timestamp=0, open=1, high=2, low=0.5, close=1.5, volume=10)
    second = Candle(timestamp=0, open=1.1, high=2.2, low=0.4, close=1.8, volume=11)
    kept = Candle(timestamp=FOUR_H, open=3, high=4, low=2, close=3.5, volume=8)
    path = tmp_path / "processed" / "BTCUSDT" / "4h.json"
    dump_candles(path, [first, second, kept], symbol="BTC/USDT", timeframe="4h")
    provider = FakeProvider({})
    report = sync_ohlcv(
        universe_path=universe,
        config=_config(tmp_path, timeframes=("4h",)),
        provider=provider,
        root=tmp_path,
        now_ms=2 * FOUR_H + 1,
        sleep_fn=lambda _d: None,
    )
    assert provider.calls == []
    assert load_candles(path) == [kept]
    assert {"reason": "duplicate_timestamp", "timestamp": 0} in report["series"][0]["issues"]


def test_unaligned_4h_cache_stops_before_provider_or_write(tmp_path: Path) -> None:
    universe = _btc_universe(tmp_path)
    path = tmp_path / "processed" / "BTCUSDT" / "4h.json"
    dump_candles(
        path,
        [Candle(timestamp=1_800_000, open=1, high=2, low=0.5, close=1.5, volume=10)],
        symbol="BTC/USDT",
        timeframe="4h",
    )
    before = path.read_bytes()
    provider = FakeProvider({("BTC/USDT", "4h"): [_c(0)]})
    with pytest.raises(UnalignedTimestampError) as exc_info:
        sync_ohlcv(
            universe_path=universe,
            config=_config(tmp_path, timeframes=("4h",)),
            provider=provider,
            root=tmp_path,
            now_ms=4 * FOUR_H,
            sleep_fn=lambda _d: None,
        )
    error = exc_info.value
    assert error.symbol == "BTC/USDT"
    assert error.timeframe == "4h"
    assert error.timestamp == 1_800_000
    assert provider.calls == []
    assert path.read_bytes() == before
    assert not (tmp_path / "raw" / "binance_public" / "BTCUSDT" / "4h.json").exists()
    assert not (tmp_path / "processed" / "manifest_4h.json").exists()
    saved = json.loads((tmp_path / "processed" / "sync_report.json").read_text(encoding="utf-8"))
    assert saved["symbols_failed"] == 1
    assert saved["symbols_ok"] == 0
    assert saved["errors"][0] == {
        "symbol": "BTC/USDT",
        "timeframe": "4h",
        "reason": "UnalignedTimestampError",
        "timestamp": 1_800_000,
        "message": str(error),
    }
    assert saved["series"][0]["reason"] == "failed"


def test_sync_report_write_failure_keeps_alignment_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    universe = _btc_universe(tmp_path)
    path = tmp_path / "processed" / "BTCUSDT" / "4h.json"
    dump_candles(
        path,
        [Candle(timestamp=1_800_000, open=1, high=2, low=0.5, close=1.5, volume=10)],
        symbol="BTC/USDT",
        timeframe="4h",
    )
    before = path.read_bytes()

    def fail_report(report_path: Path, payload: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr("tradingbot.data.ohlcv.sync.atomic_write_json", fail_report)
    provider = FakeProvider({("BTC/USDT", "4h"): [_c(0)]})
    with pytest.raises(UnalignedTimestampError) as exc_info:
        sync_ohlcv(
            universe_path=universe,
            config=_config(tmp_path, timeframes=("4h",)),
            provider=provider,
            root=tmp_path,
            now_ms=4 * FOUR_H,
            sleep_fn=lambda _d: None,
        )
    error = exc_info.value
    assert error.symbol == "BTC/USDT"
    assert error.timeframe == "4h"
    assert error.timestamp == 1_800_000
    assert isinstance(error.__cause__, OSError)
    assert "disk full" in str(error.__cause__)
    assert provider.calls == []
    assert path.read_bytes() == before
    assert not (tmp_path / "processed" / "sync_report.json").exists()
    assert not (tmp_path / "processed" / "manifest_4h.json").exists()


def _ohlcv(candle: Candle) -> tuple[int, float, float, float, float, float]:
    return (candle.timestamp, candle.open, candle.high, candle.low, candle.close, candle.volume)


def test_4h_cache_hit_skips_provider_and_keeps_ohlcv(tmp_path: Path) -> None:
    existing = [
        Candle(timestamp=0, open=1, high=2, low=0.5, close=1.5, volume=10),
        Candle(timestamp=FOUR_H, open=3, high=4, low=2, close=3.5, volume=8),
    ]
    processed = tmp_path / "processed"
    path = processed / "BTCUSDT" / "4h.json"
    dump_candles(path, existing, symbol="BTC/USDT", timeframe="4h")
    provider = FakeProvider({})
    result = sync_symbol_timeframe(
        symbol="BTC/USDT",
        timeframe="4h",
        provider=provider,
        config=_config(tmp_path, timeframes=("4h",)),
        raw_root=tmp_path / "raw",
        processed_root=processed,
        now_ms=2 * FOUR_H + 1,
        sleep_fn=lambda _delay: None,
    )
    saved = load_candles(path)
    assert provider.calls == []
    assert result["reason"] == "cache_hit"
    assert result["candles_fetched"] == 0
    assert saved[0].timestamp == existing[0].timestamp
    assert saved[-1].timestamp == existing[-1].timestamp
    assert _ohlcv(saved[0]) == _ohlcv(existing[0])
    assert _ohlcv(saved[-1]) == _ohlcv(existing[-1])
    assert saved == existing


def test_4h_one_new_closed_candle_keeps_existing_and_drops_open(tmp_path: Path) -> None:
    existing = Candle(timestamp=0, open=1, high=2, low=0.5, close=1.5, volume=10)
    duplicate = Candle(timestamp=0, open=10, high=20, low=5, close=15, volume=99)
    added = Candle(timestamp=FOUR_H, open=3, high=4, low=2, close=3.5, volume=8)
    still_open = Candle(timestamp=2 * FOUR_H, open=4, high=5, low=3, close=4.5, volume=12)
    processed = tmp_path / "processed"
    path = processed / "BTCUSDT" / "4h.json"
    dump_candles(path, [existing], symbol="BTC/USDT", timeframe="4h")
    provider = FakeProvider({("BTC/USDT", "4h"): [duplicate, added, still_open]})
    result = sync_symbol_timeframe(
        symbol="BTC/USDT",
        timeframe="4h",
        provider=provider,
        config=_config(tmp_path, timeframes=("4h",)),
        raw_root=tmp_path / "raw",
        processed_root=processed,
        now_ms=2 * FOUR_H,
        sleep_fn=lambda _delay: None,
    )
    saved = load_candles(path)
    stamps = [candle.timestamp for candle in saved]
    assert provider.calls[0]["since"] == 14_400_000
    assert result["since"] == 14_400_000
    assert _ohlcv(saved[0]) == _ohlcv(existing)
    assert saved == [existing, added]
    assert still_open.timestamp not in stamps
    assert len(stamps) == len(set(stamps))
    assert all(timestamp % 14_400_000 == 0 for timestamp in stamps)


def test_4h_repeated_page_timestamp_stops_and_keeps_existing(tmp_path: Path) -> None:
    existing = Candle(timestamp=0, open=1, high=2, low=0.5, close=1.5, volume=10)
    stuck = Candle(timestamp=0, open=9, high=9, low=9, close=9, volume=9)
    processed = tmp_path / "processed"
    path = processed / "BTCUSDT" / "4h.json"
    dump_candles(path, [existing], symbol="BTC/USDT", timeframe="4h")
    provider = FakeProvider({("BTC/USDT", "4h"): [stuck, stuck, stuck]})
    result = sync_symbol_timeframe(
        symbol="BTC/USDT",
        timeframe="4h",
        provider=provider,
        config=replace(_config(tmp_path, timeframes=("4h",)), candle_limit=3),
        raw_root=tmp_path / "raw",
        processed_root=processed,
        now_ms=4 * FOUR_H,
        sleep_fn=lambda _delay: None,
    )
    assert len(provider.calls) == 2
    assert result["reason"] == "fetched"
    assert load_candles(path) == [existing]


def test_4h_symbol_failure_preserves_its_cache_and_updates_other(tmp_path: Path) -> None:
    existing_eth = Candle(timestamp=0, open=1, high=2, low=0.5, close=1.5, volume=10)
    existing_btc = Candle(timestamp=0, open=3, high=4, low=2, close=3.5, volume=8)
    added = Candle(timestamp=FOUR_H, open=5, high=6, low=4, close=5.5, volume=12)
    universe = tmp_path / "universe.json"
    atomic_write_json(
        universe,
        {
            "universe": [
                {"binance_symbol": "ETH/USDT"},
                {"binance_symbol": "BTC/USDT"},
            ]
        },
    )
    eth_path = tmp_path / "processed" / "ETHUSDT" / "4h.json"
    btc_path = tmp_path / "processed" / "BTCUSDT" / "4h.json"
    dump_candles(eth_path, [existing_eth], symbol="ETH/USDT", timeframe="4h")
    dump_candles(btc_path, [existing_btc], symbol="BTC/USDT", timeframe="4h")
    eth_before = eth_path.read_bytes()
    provider = FakeProvider(
        {
            ("ETH/USDT", "4h"): ccxt.NetworkError("nope"),
            ("BTC/USDT", "4h"): [added],
        }
    )
    report = sync_ohlcv(
        universe_path=universe,
        config=_config(tmp_path, timeframes=("4h",)),
        provider=provider,
        root=tmp_path,
        now_ms=2 * FOUR_H,
        sleep_fn=lambda _delay: None,
    )
    saved = json.loads((tmp_path / "processed" / "sync_report.json").read_text(encoding="utf-8"))
    assert eth_path.read_bytes() == eth_before
    assert load_candles(btc_path) == [existing_btc, added]
    assert report["symbols_ok"] == 1
    assert report["symbols_failed"] == 1
    assert saved["errors"] == [
        {
            "symbol": "ETH/USDT",
            "timeframe": "4h",
            "reason": "NetworkError",
            "message": "nope",
        }
    ]
    assert saved["series"][0]["symbol"] == "ETH/USDT"
    assert saved["series"][0]["reason"] == "failed"
    assert saved["series"][1]["symbol"] == "BTC/USDT"
    assert saved["series"][1]["reason"] == "fetched"


import json
from pathlib import Path

import ccxt

from tradingbot.data.io import atomic_write_json
from tradingbot.data.ohlcv.models import Candle
from tradingbot.data.ohlcv.store import dump_candles, load_candles
from tradingbot.data.ohlcv.sync import OHLCVConfig, sync_ohlcv

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


from pathlib import Path

import ccxt
import pytest

from tradingbot.data.io import atomic_write_json
from tradingbot.data.ohlcv.models import Candle
from tradingbot.data.ohlcv.store import load_candles
from tradingbot.data.ohlcv.sync import OHLCVConfig, load_ohlcv_config, sync_ohlcv

HOUR = 3_600_000


def _c(ts: int) -> Candle:
    return Candle(timestamp=ts, open=1, high=2, low=0.5, close=1.5, volume=10)


def _series(count: int) -> list[Candle]:
    return [_c(i * HOUR) for i in range(count)]


def _config(candle_limit: int = 10, retry_max: int = 2) -> OHLCVConfig:
    return OHLCVConfig(
        provider="binance_public",
        timeframes=("1h",),
        candle_limit=candle_limit,
        raw_dir="raw",
        processed_dir="processed",
        retry_max=retry_max,
        backoff_seconds=0.01,
        drop_incomplete=True,
    )


def _universe(tmp_path: Path, symbols: list[str] | None = None) -> Path:
    path = tmp_path / "universe.json"
    atomic_write_json(
        path,
        {"universe": [{"binance_symbol": item} for item in (symbols or ["BTC/USDT"])]},
    )
    return path


class SliceProvider:
    def __init__(self, candles: list[Candle]) -> None:
        self.candles = candles
        self.calls: list[dict] = []

    def fetch_ohlcv(self, symbol, timeframe, *, since=None, limit=None):
        self.calls.append(
            {"symbol": symbol, "timeframe": timeframe, "since": since, "limit": limit}
        )
        data = self.candles
        if since is not None:
            data = [item for item in data if item.timestamp >= since]
        return data[:limit]


class ScriptedProvider:
    def __init__(self, pages: list[list[Candle] | Exception]) -> None:
        self.pages = list(pages)
        self.calls: list[dict] = []
        self.index = 0

    def fetch_ohlcv(self, symbol, timeframe, *, since=None, limit=None):
        self.calls.append({"since": since, "limit": limit})
        if self.index >= len(self.pages):
            return []
        page = self.pages[self.index]
        self.index += 1
        if isinstance(page, Exception):
            raise page
        return page


def test_short_range_makes_one_provider_call(tmp_path: Path) -> None:
    candles = _series(5)
    provider = SliceProvider(candles)
    now = 20 * HOUR
    report = sync_ohlcv(
        universe_path=_universe(tmp_path),
        config=_config(candle_limit=10),
        provider=provider,
        root=tmp_path,
        now_ms=now,
        sleep_fn=lambda _d: None,
    )
    assert len(provider.calls) == 1
    assert provider.calls[0]["limit"] == 10
    assert provider.calls[0]["limit"] <= 1000
    assert report["candles_fetched"] == 5
    assert [c.timestamp for c in load_candles(tmp_path / "processed" / "BTCUSDT" / "1h.json")] == [
        i * HOUR for i in range(5)
    ]


def test_long_range_paginates_with_advancing_since(tmp_path: Path) -> None:
    candles = _series(25)
    provider = SliceProvider(candles)
    now = 40 * HOUR
    report = sync_ohlcv(
        universe_path=_universe(tmp_path),
        config=_config(candle_limit=10),
        provider=provider,
        root=tmp_path,
        now_ms=now,
        sleep_fn=lambda _d: None,
    )
    assert len(provider.calls) == 3
    assert [call["since"] for call in provider.calls] == [None, 10 * HOUR, 20 * HOUR]
    assert all(call["limit"] == 10 for call in provider.calls)
    processed = load_candles(tmp_path / "processed" / "BTCUSDT" / "1h.json")
    assert [c.timestamp for c in processed] == [i * HOUR for i in range(25)]
    assert report["candles_fetched"] == 25


def test_repeated_last_timestamp_does_not_loop(tmp_path: Path) -> None:
    stuck = [_c(0)] * 10
    provider = ScriptedProvider([stuck, stuck, stuck])
    sync_ohlcv(
        universe_path=_universe(tmp_path),
        config=_config(candle_limit=10),
        provider=provider,
        root=tmp_path,
        now_ms=20 * HOUR,
        sleep_fn=lambda _d: None,
    )
    assert len(provider.calls) == 2


def test_duplicate_timestamps_across_pages_are_unique(tmp_path: Path) -> None:
    provider = ScriptedProvider(
        [
            [_c(0), _c(HOUR), _c(2 * HOUR)],
            [_c(2 * HOUR), _c(3 * HOUR), _c(4 * HOUR)],
        ]
    )
    config = _config(candle_limit=3)
    sync_ohlcv(
        universe_path=_universe(tmp_path),
        config=config,
        provider=provider,
        root=tmp_path,
        now_ms=20 * HOUR,
        sleep_fn=lambda _d: None,
    )
    stamps = [c.timestamp for c in load_candles(tmp_path / "processed" / "BTCUSDT" / "1h.json")]
    assert stamps == [0, HOUR, 2 * HOUR, 3 * HOUR, 4 * HOUR]
    assert len(stamps) == len(set(stamps))


def test_out_of_order_pages_are_sorted(tmp_path: Path) -> None:
    provider = ScriptedProvider(
        [
            [_c(2 * HOUR), _c(3 * HOUR)],
            [_c(0), _c(HOUR)],
        ]
    )
    sync_ohlcv(
        universe_path=_universe(tmp_path),
        config=_config(candle_limit=2),
        provider=provider,
        root=tmp_path,
        now_ms=20 * HOUR,
        sleep_fn=lambda _d: None,
    )
    stamps = [c.timestamp for c in load_candles(tmp_path / "processed" / "BTCUSDT" / "1h.json")]
    assert stamps == [0, HOUR, 2 * HOUR, 3 * HOUR]


def test_open_candle_not_written_to_processed(tmp_path: Path) -> None:
    now = 5 * HOUR
    provider = SliceProvider(_series(5) + [_c(5 * HOUR)])
    sync_ohlcv(
        universe_path=_universe(tmp_path),
        config=_config(candle_limit=100),
        provider=provider,
        root=tmp_path,
        now_ms=now,
        sleep_fn=lambda _d: None,
    )
    stamps = [c.timestamp for c in load_candles(tmp_path / "processed" / "BTCUSDT" / "1h.json")]
    assert 5 * HOUR not in stamps
    assert stamps == [i * HOUR for i in range(5)]


def test_page_error_retries_then_succeeds(tmp_path: Path) -> None:
    provider = ScriptedProvider(
        [
            _series(10),
            ccxt.RateLimitExceeded("wait"),
            ccxt.RateLimitExceeded("wait"),
            [_c(10 * HOUR), _c(11 * HOUR)],
        ]
    )
    report = sync_ohlcv(
        universe_path=_universe(tmp_path),
        config=_config(candle_limit=10, retry_max=2),
        provider=provider,
        root=tmp_path,
        now_ms=20 * HOUR,
        sleep_fn=lambda _d: None,
    )
    assert report["symbols_ok"] == 1
    assert report["symbols_failed"] == 0
    assert len(provider.calls) == 4
    stamps = [c.timestamp for c in load_candles(tmp_path / "processed" / "BTCUSDT" / "1h.json")]
    assert stamps[-1] == 11 * HOUR


def test_page_retry_exhausted_other_symbol_continues(tmp_path: Path) -> None:
    class MixedProvider:
        def __init__(self) -> None:
            self.calls: list[str] = []

        def fetch_ohlcv(self, symbol, timeframe, *, since=None, limit=None):
            self.calls.append(symbol)
            if symbol == "ETH/USDT":
                raise ccxt.NetworkError("down")
            return [_c(0), _c(HOUR)]

    provider = MixedProvider()
    report = sync_ohlcv(
        universe_path=_universe(tmp_path, ["ETH/USDT", "BTC/USDT"]),
        config=_config(candle_limit=10, retry_max=1),
        provider=provider,
        root=tmp_path,
        now_ms=4 * HOUR,
        sleep_fn=lambda _d: None,
    )
    assert report["symbols_ok"] == 1
    assert report["symbols_failed"] == 1
    assert "BTC/USDT" in {row["symbol"] for row in report["series"] if row["reason"] == "fetched"}
    assert (tmp_path / "processed" / "BTCUSDT" / "1h.json").is_file()


def test_candle_limit_over_1000_rejected(tmp_path: Path) -> None:
    path = tmp_path / "ohlcv.toml"
    path.write_text(
        'provider = "binance_public"\ntimeframes = ["1h"]\ncandle_limit = 1001\n'
        'raw_dir = "r"\nprocessed_dir = "p"\nretry_max = 1\nbackoff_seconds = 0.1\n'
        "drop_incomplete = true\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="candle_limit"):
        load_ohlcv_config(path)


def test_public_provider_clamps_limit_to_1000() -> None:
    captured: list[int | None] = []

    class Exchange:
        def fetch_ohlcv(self, symbol, timeframe, since=None, limit=None):
            captured.append(limit)
            return []

    from tradingbot.data.ohlcv.binance_ohlcv import BinancePublicOHLCVProvider

    provider = BinancePublicOHLCVProvider(exchange=Exchange())
    provider.fetch_ohlcv("BTC/USDT", "1h", limit=5000)
    assert captured == [1000]

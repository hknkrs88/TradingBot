import pytest

from tradingbot.data.ohlcv.models import Candle
from tradingbot.data.ohlcv.quality import (
    UnalignedTimestampError,
    filter_candles,
    is_incomplete,
    require_aligned_4h,
)

HOUR = 3_600_000
NOW = 10 * HOUR


def _c(ts: int, **over: float) -> Candle:
    row = {"open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5, "volume": 10.0, **over}
    return Candle(timestamp=ts, **row)


def test_duplicate_timestamp_rejected() -> None:
    candles, issues = filter_candles(
        [_c(0), _c(0, close=1.6), _c(HOUR)],
        "1h",
        now_ms=NOW,
    )
    assert [c.timestamp for c in candles] == [HOUR]
    assert any(item["reason"] == "duplicate_timestamp" for item in issues)


def test_out_of_order_is_sorted() -> None:
    candles, _ = filter_candles(
        [_c(2 * HOUR), _c(0), _c(HOUR)],
        "1h",
        now_ms=NOW,
    )
    assert [c.timestamp for c in candles] == [0, HOUR, 2 * HOUR]


def test_missing_gap_reported_not_filled() -> None:
    candles, issues = filter_candles(
        [_c(0), _c(2 * HOUR)],
        "1h",
        now_ms=NOW,
    )
    assert [c.timestamp for c in candles] == [0, 2 * HOUR]
    missing = [item for item in issues if item["reason"] == "missing_candles"]
    assert missing == [{"reason": "missing_candles", "timestamp": HOUR}]


def test_invalid_ohlc_rejected() -> None:
    _, issues = filter_candles(
        [_c(0, high=1.0, open=1.0, close=1.5, low=0.5)],
        "1h",
        now_ms=NOW,
    )
    assert any(item["reason"] == "invalid_ohlc" for item in issues)


def test_negative_volume_rejected() -> None:
    _, issues = filter_candles([_c(0, volume=-1)], "1h", now_ms=NOW)
    assert any(item["reason"] == "negative_volume" for item in issues)


def test_future_timestamp_rejected() -> None:
    _, issues = filter_candles([_c(NOW + HOUR)], "1h", now_ms=NOW)
    assert any(item["reason"] == "future_timestamp" for item in issues)


FOUR_H = 14_400_000


def test_4h_exact_close_kept_and_open_candle_dropped() -> None:
    now = 2 * FOUR_H
    candles, issues = filter_candles(
        [_c(0), _c(FOUR_H), _c(2 * FOUR_H)],
        "4h",
        now_ms=now,
    )
    assert [c.timestamp for c in candles] == [0, FOUR_H]
    assert {"reason": "incomplete_candle", "timestamp": 2 * FOUR_H} in issues


def test_4h_missing_gap_reported_not_filled() -> None:
    candles, issues = filter_candles(
        [_c(0), _c(2 * FOUR_H)],
        "4h",
        now_ms=4 * FOUR_H,
    )
    assert [c.timestamp for c in candles] == [0, 2 * FOUR_H]
    missing = [item for item in issues if item["reason"] == "missing_candles"]
    assert missing == [{"reason": "missing_candles", "timestamp": FOUR_H}]


def test_aligned_4h_timestamps_are_accepted() -> None:
    require_aligned_4h("BTC/USDT", [_c(0), _c(FOUR_H)])


def test_unaligned_4h_timestamp_raises() -> None:
    with pytest.raises(UnalignedTimestampError) as exc_info:
        require_aligned_4h("BTC/USDT", [_c(0), _c(1_800_000)])
    error = exc_info.value
    assert error.symbol == "BTC/USDT"
    assert error.timeframe == "4h"
    assert error.timestamp == 1_800_000


def test_aligned_4h_exact_close_is_closed() -> None:
    now = 2 * FOUR_H
    boundary = _c(FOUR_H)
    require_aligned_4h("BTC/USDT", [boundary])
    assert is_incomplete(boundary, FOUR_H, now) is False
    kept, _issues = filter_candles([_c(0), boundary, _c(now)], "4h", now_ms=now)
    assert [candle.timestamp for candle in kept] == [0, FOUR_H]


def test_1h_candle_off_4h_grid_is_kept() -> None:
    candles, issues = filter_candles([_c(0), _c(HOUR)], "1h", now_ms=NOW)
    assert [candle.timestamp for candle in candles] == [0, HOUR]
    assert issues == []


def test_4h_duplicate_timestamp_drops_both_rows() -> None:
    candles, issues = filter_candles(
        [_c(0), _c(0, close=1.8), _c(FOUR_H)],
        "4h",
        now_ms=3 * FOUR_H,
    )
    assert candles == [_c(FOUR_H)]
    assert {"reason": "duplicate_timestamp", "timestamp": 0} in issues

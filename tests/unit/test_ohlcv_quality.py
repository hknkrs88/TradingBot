from tradingbot.data.ohlcv.models import Candle
from tradingbot.data.ohlcv.quality import filter_candles

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

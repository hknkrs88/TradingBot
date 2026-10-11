from tradingbot.data.ohlcv.models import Candle
from tradingbot.data.ohlcv.sufficiency import (
    MIN_CLOSED_CANDLES,
    TIMEFRAME,
    assess_4h_sufficiency,
)

TF = 14_400_000
SYMBOL = "BTC/USDT"


def _c(index: int, **over: float) -> Candle:
    row = {"open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5, "volume": 10.0, **over}
    return Candle(timestamp=index * TF, **row)


def _series(count: int, *, start: int = 0) -> list[Candle]:
    return [_c(start + index) for index in range(count)]


def _assess(
    candles: list[Candle],
    *,
    now_ms: int,
    symbol: str = SYMBOL,
    source_symbol: str | None = SYMBOL,
    source_timeframe: str | None = TIMEFRAME,
):
    return assess_4h_sufficiency(
        symbol,
        candles,
        now_ms=now_ms,
        source_symbol=source_symbol,
        source_timeframe=source_timeframe,
    )


def test_49_closed_candles_are_rejected() -> None:
    candles = _series(49)
    now_ms = 49 * TF
    result = _assess(candles, now_ms=now_ms)
    assert result.eligible is False
    assert result.reason == "too_few_closed_candles"
    assert result.closed_count == 49


def test_exactly_50_closed_candles_are_eligible() -> None:
    candles = _series(50)
    now_ms = 50 * TF
    result = _assess(candles, now_ms=now_ms)
    assert result.eligible is True
    assert result.reason is None
    assert result.closed_count == MIN_CLOSED_CANDLES
    assert result.has_gaps is False
    assert result.identity_checked is True


def test_open_candle_does_not_count_toward_minimum() -> None:
    candles = _series(49) + [_c(49)]
    now_ms = 49 * TF + 1_000
    result = _assess(candles, now_ms=now_ms)
    assert result.closed_count == 49
    assert result.eligible is False
    assert result.reason == "too_few_closed_candles"


def test_future_candle_is_rejected() -> None:
    candles = _series(50)
    now_ms = 50 * TF
    candles.append(_c(60))
    result = _assess(candles, now_ms=now_ms)
    assert result.eligible is False
    assert result.reason == "future_timestamp"


def test_duplicate_timestamp_is_rejected() -> None:
    candles = _series(50)
    candles[1] = _c(0)
    result = _assess(candles, now_ms=50 * TF)
    assert result.eligible is False
    assert result.reason == "duplicate_timestamp"


def test_unaligned_timestamp_is_rejected() -> None:
    candles = _series(50)
    candles[3] = Candle(
        timestamp=3 * TF + 60_000,
        open=1.0,
        high=2.0,
        low=0.5,
        close=1.5,
        volume=10.0,
    )
    result = _assess(candles, now_ms=50 * TF)
    assert result.eligible is False
    assert result.reason == "unaligned_timestamp"


def test_symbol_mismatch_is_rejected() -> None:
    candles = _series(50)
    result = _assess(
        candles,
        now_ms=50 * TF,
        source_symbol="ETH/USDT",
    )
    assert result.eligible is False
    assert result.reason == "symbol_mismatch"


def test_gaps_are_reported_without_rejecting() -> None:
    candles = _series(49) + [_c(50)]
    now_ms = 51 * TF
    result = _assess(candles, now_ms=now_ms)
    assert result.closed_count == 50
    assert result.eligible is True
    assert result.has_gaps is True
    assert result.gap_timestamps == (49 * TF,)
    assert result.reason is None


def test_input_series_is_not_mutated() -> None:
    candles = _series(50)
    original = list(candles)
    _assess(candles, now_ms=50 * TF)
    assert candles == original
    assert [item.timestamp for item in candles] == [index * TF for index in range(50)]


def test_fixed_now_ms_is_deterministic() -> None:
    candles = _series(50)
    now_ms = 50 * TF
    first = _assess(candles, now_ms=now_ms)
    second = _assess(candles, now_ms=now_ms)
    assert first == second
    assert first.last_closed_timestamp == 49 * TF
    assert first.last_closed_age_ms == 0
    assert first.caught_up is True


def test_unsorted_timestamps_are_rejected() -> None:
    candles = _series(50)
    candles[0], candles[2] = candles[2], candles[0]
    result = _assess(candles, now_ms=50 * TF)
    assert result.eligible is False
    assert result.reason == "unsorted_timestamps"


def test_wrong_source_timeframe_is_rejected() -> None:
    candles = _series(50)
    result = _assess(candles, now_ms=50 * TF, source_timeframe="1h")
    assert result.eligible is False
    assert result.reason == "timeframe_mismatch"


def test_invalid_ohlc_is_rejected() -> None:
    candles = _series(50)
    candles[4] = _c(4, high=1.0, open=1.0, close=1.5, low=0.5)
    result = _assess(candles, now_ms=50 * TF)
    assert result.eligible is False
    assert result.reason == "invalid_ohlc"


def test_negative_volume_is_rejected() -> None:
    candles = _series(50)
    candles[4] = _c(4, volume=-1)
    result = _assess(candles, now_ms=50 * TF)
    assert result.eligible is False
    assert result.reason == "negative_volume"


def test_missing_source_identity_does_not_reject_by_itself() -> None:
    candles = _series(50)
    result = _assess(
        candles,
        now_ms=50 * TF,
        source_symbol=None,
        source_timeframe=None,
    )
    assert result.identity_checked is False
    assert result.eligible is True
    assert result.reason is None


def test_empty_series_is_rejected_safely() -> None:
    result = _assess([], now_ms=50 * TF)
    assert result.eligible is False
    assert result.reason == "too_few_closed_candles"
    assert result.closed_count == 0
    assert result.last_closed_timestamp is None
    assert result.last_closed_age_ms is None
    assert result.has_gaps is False

import math

import pytest

from tradingbot.data.ohlcv.models import Candle
from tradingbot.indicators import (
    RSI_PERIOD,
    SKIP_NON_FINITE,
    SKIP_UNDEFINED,
    SKIP_WARMUP,
    STOCH_RSI_D_SMOOTH,
    STOCH_RSI_K_SMOOTH,
    STOCH_RSI_WINDOW,
    rsi,
    stoch_rsi,
)
from tradingbot.strategy.models import CandleSeries

TF = 14_400_000
SYMBOL = "AAA/USDT"


def _bar(index: int, close: float, *, start: int = 0) -> Candle:
    ts = (start + index) * TF
    return Candle(timestamp=ts, open=close, high=close, low=close, close=close, volume=1.0)


def _series(closes: list[float], *, start: int = 0) -> CandleSeries:
    candles = tuple(_bar(i, close, start=start) for i, close in enumerate(closes))
    return CandleSeries.from_closed(
        symbol=SYMBOL,
        timeframe="4h",
        candles=candles,
        now_ms=candles[-1].timestamp + TF,
    )


def test_rsi_wilder_hand_calculated_period_3() -> None:
    result = rsi(_series([1.0, 2.0, 3.0, 2.0, 4.0]), 3)
    assert [p.skip_reason for p in result.points[:3]] == [SKIP_WARMUP, SKIP_WARMUP, SKIP_WARMUP]
    assert result.points[3].value == pytest.approx(100.0 - 100.0 / 3.0)
    assert result.points[4].value == pytest.approx(100.0 - 100.0 / 6.0)
    assert result.name == "rsi_3"


def test_rsi_rising_is_100_falling_is_0() -> None:
    rising = rsi(_series([1.0, 2.0, 3.0, 4.0, 5.0]), 3)
    assert rising.points[3].value == pytest.approx(100.0)
    assert rising.points[4].value == pytest.approx(100.0)
    falling = rsi(_series([5.0, 4.0, 3.0, 2.0, 1.0]), 3)
    assert falling.points[3].value == pytest.approx(0.0)
    assert falling.points[4].value == pytest.approx(0.0)


def test_rsi_unchanged_closes_are_undefined() -> None:
    result = rsi(_series([2.0, 2.0, 2.0, 2.0, 2.0]), 3)
    assert result.points[3].skip_reason == SKIP_UNDEFINED
    assert result.points[4].skip_reason == SKIP_UNDEFINED


def test_rsi_gap_does_not_use_pre_gap_diffs() -> None:
    pre = [_bar(i, float(i + 1)) for i in range(4)]
    post = [_bar(i, 10.0 + i, start=8) for i in range(5)]
    candles = tuple(pre + post)
    series = CandleSeries.from_closed(
        symbol=SYMBOL, timeframe="4h", candles=candles, now_ms=candles[-1].timestamp + TF
    )
    result = rsi(series, 3)
    assert result.points[3].value == pytest.approx(100.0)
    assert result.points[4].skip_reason == SKIP_WARMUP
    assert result.points[6].skip_reason == SKIP_WARMUP
    assert result.points[7].value == pytest.approx(100.0)


def test_rsi_nan_and_inf_are_non_finite() -> None:
    candles = (
        _bar(0, 1.0),
        _bar(1, math.nan),
        _bar(2, math.inf),
        _bar(3, 2.0),
        _bar(4, 3.0),
        _bar(5, 4.0),
        _bar(6, 5.0),
    )
    series = CandleSeries.from_closed(
        symbol=SYMBOL, timeframe="4h", candles=candles, now_ms=candles[-1].timestamp + TF
    )
    result = rsi(series, 3)
    assert result.points[1].skip_reason == SKIP_NON_FINITE
    assert result.points[2].skip_reason == SKIP_NON_FINITE
    assert result.points[6].value == pytest.approx(100.0)


def test_rsi_prefix_matches_full_series() -> None:
    full = _series([1.0, 2.0, 3.0, 2.0, 4.0, 3.5, 5.0])
    full_rsi = rsi(full, 3)
    for end in range(1, len(full.candles) + 1):
        prefix = CandleSeries.from_closed(
            symbol=SYMBOL,
            timeframe="4h",
            candles=full.candles[:end],
            now_ms=full.candles[end - 1].timestamp + TF,
        )
        assert rsi(prefix, 3).points[-1] == full_rsi.points[end - 1]


def test_stoch_rsi_known_window_k_and_d() -> None:
    result = stoch_rsi(
        _series([10.0, 11.0, 12.0, 10.0, 11.0]),
        rsi_period=2,
        window=2,
        k_smooth=1,
        d_smooth=1,
    )
    assert result.k.points[3].value == pytest.approx(0.0)
    assert result.d.points[3].value == pytest.approx(0.0)
    assert result.k.points[4].value == pytest.approx(100.0)
    assert result.d.points[4].value == pytest.approx(100.0)


def test_stoch_rsi_equal_window_is_undefined() -> None:
    result = stoch_rsi(
        _series([1.0, 2.0, 3.0, 4.0, 5.0, 6.0]),
        rsi_period=2,
        window=2,
        k_smooth=1,
        d_smooth=1,
    )
    assert result.k.points[2].skip_reason == SKIP_WARMUP
    assert result.k.points[3].skip_reason == SKIP_UNDEFINED
    assert result.d.points[3].skip_reason == SKIP_UNDEFINED


def test_stoch_rsi_k_then_d_smoothing_warmup() -> None:
    result = stoch_rsi(
        _series([10.0, 12.0, 9.0, 13.0, 8.0, 14.0, 7.0, 15.0]),
        rsi_period=2,
        window=2,
        k_smooth=3,
        d_smooth=3,
    )
    first_k = next(i for i, p in enumerate(result.k.points) if p.value is not None)
    first_d = next(i for i, p in enumerate(result.d.points) if p.value is not None)
    assert first_d == first_k + 2
    assert result.d.points[first_d].value == pytest.approx(
        sum(p.value for p in result.k.points[first_k : first_d + 1]) / 3.0
    )


def test_stoch_rsi_default_warmup_length() -> None:
    closes = [50.0 + 12.0 * math.sin(index / 3.0) for index in range(40)]
    result = stoch_rsi(_series(closes))
    first_rsi = RSI_PERIOD
    first_raw = first_rsi + STOCH_RSI_WINDOW - 1
    first_k = first_raw + STOCH_RSI_K_SMOOTH - 1
    first_d = first_k + STOCH_RSI_D_SMOOTH - 1
    assert first_d == 31
    assert all(p.value is None for p in result.d.points[:first_d])
    assert result.d.points[first_d].value is not None
    assert result.k.points[first_k].value is not None
    assert result.k.points[first_k - 1].skip_reason == SKIP_WARMUP


def test_stoch_rsi_propagates_rsi_gap_and_prefix() -> None:
    pre = [_bar(i, 10.0 + i) for i in range(5)]
    post = [_bar(i, 20.0 - i, start=10) for i in range(6)]
    candles = tuple(pre + post)
    full = CandleSeries.from_closed(
        symbol=SYMBOL, timeframe="4h", candles=candles, now_ms=candles[-1].timestamp + TF
    )
    result = stoch_rsi(full, rsi_period=2, window=2, k_smooth=1, d_smooth=1)
    assert result.k.points[5].skip_reason == SKIP_WARMUP
    full_k = result.k
    prefix = CandleSeries.from_closed(
        symbol=SYMBOL, timeframe="4h", candles=candles[:8], now_ms=candles[7].timestamp + TF
    )
    assert stoch_rsi(prefix, rsi_period=2, window=2, k_smooth=1, d_smooth=1).k.points[-1] == full_k.points[7]


def test_stoch_rsi_nan_close_is_non_finite() -> None:
    candles = (_bar(0, 1.0), _bar(1, math.nan), _bar(2, 2.0), _bar(3, 3.0), _bar(4, 4.0))
    series = CandleSeries.from_closed(
        symbol=SYMBOL, timeframe="4h", candles=candles, now_ms=candles[-1].timestamp + TF
    )
    result = stoch_rsi(series, rsi_period=2, window=2, k_smooth=1, d_smooth=1)
    assert result.k.points[1].skip_reason == SKIP_NON_FINITE
    assert result.d.points[1].skip_reason == SKIP_NON_FINITE

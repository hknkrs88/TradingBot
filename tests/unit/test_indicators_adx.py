import math

import pytest

from tradingbot.data.ohlcv.models import Candle
from tradingbot.indicators import (
    ADX_PERIOD,
    SKIP_NON_FINITE,
    SKIP_UNDEFINED,
    SKIP_WARMUP,
    adx,
    directional_moves,
    true_range,
)
from tradingbot.strategy.models import CandleSeries

TF = 14_400_000
SYMBOL = "AAA/USDT"


def _c(
    index: int,
    high: float,
    low: float,
    close: float,
    *,
    start: int = 0,
) -> Candle:
    return Candle(
        timestamp=(start + index) * TF,
        open=close,
        high=high,
        low=low,
        close=close,
        volume=1.0,
    )


def _series(candles: tuple[Candle, ...]) -> CandleSeries:
    return CandleSeries.from_closed(
        symbol=SYMBOL,
        timeframe="4h",
        candles=candles,
        now_ms=candles[-1].timestamp + TF,
    )


def test_directional_moves_up_down_and_equal() -> None:
    prev = _c(0, 10.0, 8.0, 9.0)
    up = _c(1, 12.0, 9.0, 11.0)
    down = _c(1, 9.0, 7.0, 8.0)
    equal = _c(1, 12.0, 6.0, 9.0)
    assert directional_moves(up, prev) == (2.0, 0.0)
    assert directional_moves(down, prev) == (0.0, 1.0)
    assert directional_moves(equal, prev) == (0.0, 0.0)


def test_true_range_includes_previous_close() -> None:
    candle = _c(1, 10.0, 9.0, 9.5)
    assert true_range(candle, 12.0) == pytest.approx(3.0)


def test_adx_hand_calculated_period_2() -> None:
    candles = (
        _c(0, 10.0, 8.0, 9.0),
        _c(1, 12.0, 9.0, 11.0),
        _c(2, 13.0, 10.0, 12.0),
        _c(3, 14.0, 11.0, 13.0),
    )
    result = adx(_series(candles), 2)
    assert result.plus_di.points[2].value == pytest.approx(50.0)
    assert result.minus_di.points[2].value == pytest.approx(0.0)
    assert result.dx.points[2].value == pytest.approx(100.0)
    assert result.adx.points[2].skip_reason == SKIP_WARMUP
    assert result.adx.points[3].value == pytest.approx(100.0)


def test_adx_zero_tr_is_undefined() -> None:
    candles = (_c(0, 5.0, 5.0, 5.0), _c(1, 5.0, 5.0, 5.0))
    result = adx(_series(candles), 1)
    assert result.plus_di.points[1].skip_reason == SKIP_UNDEFINED
    assert result.dx.points[1].skip_reason == SKIP_UNDEFINED


def test_adx_zero_di_sum_is_undefined_dx() -> None:
    candles = (
        _c(0, 10.0, 8.0, 9.0),
        _c(1, 12.0, 6.0, 9.0),
        _c(2, 14.0, 4.0, 9.0),
    )
    result = adx(_series(candles), 2)
    assert result.plus_di.points[2].value == pytest.approx(0.0)
    assert result.minus_di.points[2].value == pytest.approx(0.0)
    assert result.dx.points[2].skip_reason == SKIP_UNDEFINED
    assert result.adx.points[2].skip_reason == SKIP_UNDEFINED


def test_adx_gap_restarts_warmup() -> None:
    pre = (_c(0, 10.0, 8.0, 9.0), _c(1, 12.0, 9.0, 11.0), _c(2, 13.0, 10.0, 12.0))
    post = (_c(0, 20.0, 18.0, 19.0, start=8), _c(1, 22.0, 19.0, 21.0, start=8), _c(2, 23.0, 20.0, 22.0, start=8))
    result = adx(_series(pre + post), 2)
    assert result.plus_di.points[2].value is not None
    assert result.plus_di.points[3].skip_reason == SKIP_WARMUP
    assert result.plus_di.points[5].value is not None


def test_adx_nan_and_invalid_ohlc() -> None:
    nan_bar = Candle(timestamp=TF, open=1.0, high=math.nan, low=1.0, close=1.0, volume=1.0)
    bad = _c(1, 1.0, 3.0, 2.0)
    series = _series((_c(0, 2.0, 1.0, 1.5), nan_bar))
    assert adx(series, 2).plus_di.points[1].skip_reason == SKIP_NON_FINITE
    invalid = _series((_c(0, 2.0, 1.0, 1.5), bad))
    assert adx(invalid, 2).plus_di.points[1].skip_reason == SKIP_UNDEFINED


def test_adx_default_first_valid_indices() -> None:
    candles = tuple(_c(i, 10.0 + i, 8.0 + i, 9.0 + i) for i in range(32))
    result = adx(_series(candles))
    first_di = ADX_PERIOD
    first_adx = first_di + ADX_PERIOD - 1
    assert first_di == 14
    assert first_adx == 27
    assert result.plus_di.points[first_di - 1].skip_reason == SKIP_WARMUP
    assert result.plus_di.points[first_di].value is not None
    assert result.adx.points[first_adx - 1].skip_reason == SKIP_WARMUP
    assert result.adx.points[first_adx].value is not None


def test_adx_prefix_matches_full_series() -> None:
    candles = tuple(_c(i, 10.0 + i * 0.5, 8.0 + i * 0.4, 9.0 + i * 0.45) for i in range(12))
    full = adx(_series(candles), 3)
    for end in range(2, len(candles) + 1):
        prefix = _series(candles[:end])
        assert adx(prefix, 3).plus_di.points[-1] == full.plus_di.points[end - 1]
        assert adx(prefix, 3).adx.points[-1] == full.adx.points[end - 1]

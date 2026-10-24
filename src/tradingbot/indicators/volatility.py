"""ATR and Bollinger Bands. No orders or scoring."""

from __future__ import annotations

import math
from dataclasses import dataclass

from tradingbot.indicators.common import (
    SKIP_NON_FINITE,
    SKIP_UNDEFINED,
    SKIP_WARMUP,
    close_is_finite,
    consecutive,
    ohlc_is_finite,
    require_period,
    series_from_points,
    skip_point,
    timeframe_ms,
    value_point,
    window_is_consecutive,
)
from tradingbot.indicators.models import IndicatorPoint, IndicatorSeries
from tradingbot.indicators.strength import true_range
from tradingbot.indicators.trend import sma
from tradingbot.strategy.models import CandleSeries

ATR_PERIOD = 14
BB_PERIOD = 20
BB_STD_MULTIPLIER = 2.0


def _invalid_ohlc(candle) -> bool:
    return candle.high < candle.low


def population_stdev(values: list[float], mean: float) -> float:
    """Population standard deviation (divide by n, not n-1)."""
    if not values:
        raise ValueError("values must be non-empty")
    variance = sum((item - mean) ** 2 for item in values) / float(len(values))
    return math.sqrt(variance)


def _require_multiplier(value: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("std multiplier must be a finite number > 0")
    multiplier = float(value)
    if not math.isfinite(multiplier) or multiplier <= 0.0:
        raise ValueError("std multiplier must be a finite number > 0")
    return multiplier


def atr(series: CandleSeries, period: int = ATR_PERIOD) -> IndicatorSeries:
    """Wilder ATR. Seeded by the SMA of the first ``period`` true ranges of a run.

    Zero ATR is a valid value. First value is at index ``period`` (period TR samples).
    """
    period = require_period(period)
    tf_ms = timeframe_ms(series.timeframe)
    points: list[IndicatorPoint] = []
    previous = None
    seed: list[float] = []
    average: float | None = None
    for candle in series.candles:
        ts = candle.timestamp
        if not ohlc_is_finite(candle):
            points.append(skip_point(ts, SKIP_NON_FINITE))
            previous = None
            seed = []
            average = None
            continue
        if _invalid_ohlc(candle):
            points.append(skip_point(ts, SKIP_UNDEFINED))
            previous = None
            seed = []
            average = None
            continue
        attached = previous is not None and consecutive(previous, candle, tf_ms)
        if not attached:
            previous = candle
            seed = []
            average = None
            points.append(skip_point(ts, SKIP_WARMUP))
            continue
        tr = true_range(candle, previous.close)
        previous = candle
        if average is None:
            seed.append(tr)
            if len(seed) < period:
                points.append(skip_point(ts, SKIP_WARMUP))
                continue
            average = sum(seed) / float(period)
            points.append(value_point(ts, average))
            continue
        average = (average * (period - 1) + tr) / float(period)
        points.append(value_point(ts, average))
    return series_from_points(name=f"atr_{period}", series=series, points=points)


@dataclass(frozen=True)
class BollingerResult:
    """Middle is SMA. Upper/lower use population stdev. Width is a ratio."""

    middle: IndicatorSeries
    upper: IndicatorSeries
    lower: IndicatorSeries
    width: IndicatorSeries


def bollinger_bands(
    series: CandleSeries,
    *,
    period: int = BB_PERIOD,
    std_multiplier: float = BB_STD_MULTIPLIER,
) -> BollingerResult:
    """Bollinger Bands: SMA ± k × population standard deviation of the same window."""
    period = require_period(period)
    k = _require_multiplier(std_multiplier)
    tf_ms = timeframe_ms(series.timeframe)
    middle = sma(series, period)
    upper_pts: list[IndicatorPoint] = []
    lower_pts: list[IndicatorPoint] = []
    width_pts: list[IndicatorPoint] = []
    candles = series.candles
    for index, mid in enumerate(middle.points):
        ts = mid.timestamp
        if mid.value is None:
            reason = mid.skip_reason or SKIP_WARMUP
            upper_pts.append(skip_point(ts, reason))
            lower_pts.append(skip_point(ts, reason))
            width_pts.append(skip_point(ts, reason))
            continue
        window = candles[index - period + 1 : index + 1]
        if len(window) != period or not window_is_consecutive(window, tf_ms):
            upper_pts.append(skip_point(ts, SKIP_WARMUP))
            lower_pts.append(skip_point(ts, SKIP_WARMUP))
            width_pts.append(skip_point(ts, SKIP_WARMUP))
            continue
        if any(not close_is_finite(item) for item in window):
            upper_pts.append(skip_point(ts, SKIP_NON_FINITE))
            lower_pts.append(skip_point(ts, SKIP_NON_FINITE))
            width_pts.append(skip_point(ts, SKIP_NON_FINITE))
            continue
        closes = [item.close for item in window]
        stdev = population_stdev(closes, mid.value)
        upper = mid.value + k * stdev
        lower = mid.value - k * stdev
        upper_pts.append(value_point(ts, upper))
        lower_pts.append(value_point(ts, lower))
        if mid.value == 0.0:
            width_pts.append(skip_point(ts, SKIP_UNDEFINED))
            continue
        width_pts.append(value_point(ts, (upper - lower) / mid.value))
    return BollingerResult(
        middle=middle,
        upper=series_from_points(name="bb_upper", series=series, points=upper_pts),
        lower=series_from_points(name="bb_lower", series=series, points=lower_pts),
        width=series_from_points(name="bb_width", series=series, points=width_pts),
    )

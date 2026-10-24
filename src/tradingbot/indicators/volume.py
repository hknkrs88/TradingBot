"""Base-asset volume indicators. Not quote volume, spread, or book depth.

``Candle.volume`` is the base-asset quantity from processed OHLCV. These
functions never treat ``close * volume`` as quote turnover. OBV is a
cumulative signed base-volume path and is not comparable across symbols as a
liquidity score.
"""

from __future__ import annotations

import math

from tradingbot.data.ohlcv.models import Candle
from tradingbot.indicators.common import (
    SKIP_GAP,
    SKIP_NON_FINITE,
    SKIP_UNDEFINED,
    SKIP_WARMUP,
    close_is_finite,
    consecutive,
    require_period,
    series_from_points,
    skip_point,
    timeframe_ms,
    value_point,
    window_is_consecutive,
)
from tradingbot.indicators.models import IndicatorPoint, IndicatorSeries
from tradingbot.strategy.models import CandleSeries

VOLUME_SMA_PERIOD = 20


def _base_volume_skip(candle: Candle) -> str | None:
    if not math.isfinite(candle.volume):
        return SKIP_NON_FINITE
    if candle.volume < 0.0:
        return SKIP_UNDEFINED
    return None


def volume_sma(series: CandleSeries, period: int = VOLUME_SMA_PERIOD) -> IndicatorSeries:
    """SMA of base-asset ``Candle.volume``. Zero volume is valid; negative is not."""
    period = require_period(period)
    tf_ms = timeframe_ms(series.timeframe)
    points: list[IndicatorPoint] = []
    candles = series.candles
    for index, candle in enumerate(candles):
        reason = _base_volume_skip(candle)
        if reason is not None:
            points.append(skip_point(candle.timestamp, reason))
            continue
        if index + 1 < period:
            points.append(skip_point(candle.timestamp, SKIP_WARMUP))
            continue
        window = candles[index - period + 1 : index + 1]
        if not window_is_consecutive(window, tf_ms):
            points.append(skip_point(candle.timestamp, SKIP_WARMUP))
            continue
        window_skip = next((item for item in window if _base_volume_skip(item) is not None), None)
        if window_skip is not None:
            points.append(skip_point(candle.timestamp, _base_volume_skip(window_skip) or SKIP_NON_FINITE))
            continue
        mean = sum(item.volume for item in window) / float(period)
        points.append(value_point(candle.timestamp, mean))
    return series_from_points(name=f"volume_sma_{period}", series=series, points=points)


def obv(series: CandleSeries) -> IndicatorSeries:
    """On-balance base volume. Starts at 0 after the first bar of a consecutive run.

    The first valid index in a run is 1: previous close is required. A gap or
    invalid bar resets the cumulative total; prior OBV is not carried across.
    """
    tf_ms = timeframe_ms(series.timeframe)
    points: list[IndicatorPoint] = []
    previous: Candle | None = None
    total = 0.0
    for candle in series.candles:
        vol_skip = _base_volume_skip(candle)
        if vol_skip is not None:
            points.append(skip_point(candle.timestamp, vol_skip))
            previous = None
            total = 0.0
            continue
        if not close_is_finite(candle):
            points.append(skip_point(candle.timestamp, SKIP_NON_FINITE))
            previous = None
            total = 0.0
            continue
        attached = previous is not None and consecutive(previous, candle, tf_ms)
        if not attached:
            previous = candle
            total = 0.0
            points.append(skip_point(candle.timestamp, SKIP_WARMUP))
            continue
        if candle.close > previous.close:
            total += candle.volume
        elif candle.close < previous.close:
            total -= candle.volume
        previous = candle
        points.append(value_point(candle.timestamp, total))
    return series_from_points(name="obv", series=series, points=points)


def volume_roc(series: CandleSeries, period: int) -> IndicatorSeries:
    """``base_volume[t] / base_volume[t-period] - 1``. Ratio, not percent."""
    period = require_period(period)
    tf_ms = timeframe_ms(series.timeframe)
    points: list[IndicatorPoint] = []
    candles = series.candles
    for index, candle in enumerate(candles):
        reason = _base_volume_skip(candle)
        if reason is not None:
            points.append(skip_point(candle.timestamp, reason))
            continue
        if index < period:
            points.append(skip_point(candle.timestamp, SKIP_WARMUP))
            continue
        lookback = candles[index - period]
        span = candle.timestamp - lookback.timestamp
        if span != period * tf_ms:
            points.append(skip_point(candle.timestamp, SKIP_GAP))
            continue
        lookback_skip = _base_volume_skip(lookback)
        if lookback_skip is not None:
            points.append(skip_point(candle.timestamp, lookback_skip))
            continue
        if lookback.volume == 0.0:
            points.append(skip_point(candle.timestamp, SKIP_UNDEFINED))
            continue
        ratio = candle.volume / lookback.volume - 1.0
        points.append(value_point(candle.timestamp, ratio))
    return series_from_points(name=f"volume_roc_{period}", series=series, points=points)

"""Shared indicator helpers. No ranking, simulation, or orders."""

from __future__ import annotations

import math

from tradingbot.data.ohlcv.models import Candle
from tradingbot.data.ohlcv.timeframes import timeframe_ms
from tradingbot.indicators.models import (
    SKIP_GAP,
    SKIP_NON_FINITE,
    SKIP_UNDEFINED,
    SKIP_WARMUP,
    IndicatorPoint,
    IndicatorSeries,
)
from tradingbot.strategy.models import CandleSeries


def require_period(period: int) -> int:
    if isinstance(period, bool) or not isinstance(period, int) or period < 1:
        raise ValueError("period must be an integer >= 1")
    return period


def close_is_finite(candle: Candle) -> bool:
    return math.isfinite(candle.close)


def ohlc_is_finite(candle: Candle) -> bool:
    return (
        math.isfinite(candle.open)
        and math.isfinite(candle.high)
        and math.isfinite(candle.low)
        and math.isfinite(candle.close)
    )


def consecutive(left: Candle, right: Candle, tf_ms: int) -> bool:
    return right.timestamp - left.timestamp == tf_ms


def skip_point(timestamp: int, reason: str) -> IndicatorPoint:
    return IndicatorPoint(timestamp=timestamp, value=None, skip_reason=reason)


def value_point(timestamp: int, value: float) -> IndicatorPoint:
    if not math.isfinite(value):
        return skip_point(timestamp, SKIP_NON_FINITE)
    return IndicatorPoint(timestamp=timestamp, value=value)


def series_from_points(
    *,
    name: str,
    series: CandleSeries,
    points: list[IndicatorPoint],
) -> IndicatorSeries:
    return IndicatorSeries(
        name=name,
        symbol=series.symbol,
        timeframe=series.timeframe,
        points=tuple(points),
    )


def window_is_consecutive(window: tuple[Candle, ...] | list[Candle], tf_ms: int) -> bool:
    if not window:
        return False
    first = window[0].timestamp
    last = window[-1].timestamp
    return last - first == (len(window) - 1) * tf_ms


__all__ = [
    "SKIP_GAP",
    "SKIP_NON_FINITE",
    "SKIP_UNDEFINED",
    "SKIP_WARMUP",
    "close_is_finite",
    "consecutive",
    "ohlc_is_finite",
    "require_period",
    "series_from_points",
    "skip_point",
    "timeframe_ms",
    "value_point",
    "window_is_consecutive",
]

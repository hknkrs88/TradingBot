"""Timestamp alignment and relative simple returns. Research only.

Relative return is ``return_A - return_B`` where each simple return is
``close_t / close_baseline - 1``. It is not a liquidity or tradability score
and is not a price ratio or log return.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from tradingbot.data.ohlcv.models import Candle
from tradingbot.indicators.common import (
    SKIP_NON_FINITE,
    SKIP_UNDEFINED,
    series_from_points,
    skip_point,
    value_point,
)
from tradingbot.indicators.models import IndicatorPoint, IndicatorSeries
from tradingbot.strategy.models import CandleSeries


@dataclass(frozen=True)
class AlignedIndicatorPair:
    """Intersection of two indicator series. No fill, no timestamp rounding."""

    left_symbol: str
    right_symbol: str
    timeframe: str
    timestamps: tuple[int, ...]
    left: tuple[IndicatorPoint, ...]
    right: tuple[IndicatorPoint, ...]
    left_only: tuple[int, ...]
    right_only: tuple[int, ...]


@dataclass(frozen=True)
class RelativeReturnResult:
    """Simple returns vs a shared baseline close, then A minus B at common times."""

    symbol_a: str
    symbol_b: str
    timeframe: str
    baseline_timestamp: int | None
    return_a: IndicatorSeries
    return_b: IndicatorSeries
    relative: IndicatorSeries


def align_indicator_series(left: IndicatorSeries, right: IndicatorSeries) -> AlignedIndicatorPair:
    """Return chronologically sorted timestamp intersection. Raises on timeframe mismatch."""
    if left.timeframe != right.timeframe:
        raise ValueError(
            f"timeframe mismatch: {left.timeframe!r} != {right.timeframe!r}"
        )
    left_map = {point.timestamp: point for point in left.points}
    right_map = {point.timestamp: point for point in right.points}
    left_ts = set(left_map)
    right_ts = set(right_map)
    common = tuple(sorted(left_ts & right_ts))
    return AlignedIndicatorPair(
        left_symbol=left.symbol,
        right_symbol=right.symbol,
        timeframe=left.timeframe,
        timestamps=common,
        left=tuple(left_map[ts] for ts in common),
        right=tuple(right_map[ts] for ts in common),
        left_only=tuple(sorted(left_ts - right_ts)),
        right_only=tuple(sorted(right_ts - left_ts)),
    )


def _close_ok(close: float) -> bool:
    return math.isfinite(close) and close > 0.0


def _candle_map(series: CandleSeries) -> dict[int, Candle]:
    return {candle.timestamp: candle for candle in series.candles}


def _simple_return_point(timestamp: int, close: float, baseline: float) -> IndicatorPoint:
    if not math.isfinite(close):
        return skip_point(timestamp, SKIP_NON_FINITE)
    if not _close_ok(baseline):
        return skip_point(timestamp, SKIP_UNDEFINED)
    if close <= 0.0:
        return skip_point(timestamp, SKIP_UNDEFINED)
    return value_point(timestamp, close / baseline - 1.0)


def relative_return(series_a: CandleSeries, series_b: CandleSeries) -> RelativeReturnResult:
    """Compare two symbols on the intersection of candle timestamps.

    Baseline is the earliest common timestamp. Simple return is
    ``close_t / close_baseline - 1``. Relative return is ``return_a - return_b``.
    """
    if series_a.timeframe != series_b.timeframe:
        raise ValueError(
            f"timeframe mismatch: {series_a.timeframe!r} != {series_b.timeframe!r}"
        )
    map_a = _candle_map(series_a)
    map_b = _candle_map(series_b)
    common = tuple(sorted(set(map_a) & set(map_b)))
    pair_symbol = f"{series_a.symbol}|{series_b.symbol}"
    if not common:
        empty_a = series_from_points(name="simple_return", series=series_a, points=[])
        empty_b = series_from_points(name="simple_return", series=series_b, points=[])
        empty_rel = IndicatorSeries(
            name="relative_return",
            symbol=pair_symbol,
            timeframe=series_a.timeframe,
            points=(),
        )
        return RelativeReturnResult(
            symbol_a=series_a.symbol,
            symbol_b=series_b.symbol,
            timeframe=series_a.timeframe,
            baseline_timestamp=None,
            return_a=empty_a,
            return_b=empty_b,
            relative=empty_rel,
        )
    baseline_ts = common[0]
    base_a = map_a[baseline_ts].close
    base_b = map_b[baseline_ts].close
    points_a: list[IndicatorPoint] = []
    points_b: list[IndicatorPoint] = []
    points_rel: list[IndicatorPoint] = []
    for ts in common:
        close_a = map_a[ts].close
        close_b = map_b[ts].close
        point_a = _simple_return_point(ts, close_a, base_a)
        point_b = _simple_return_point(ts, close_b, base_b)
        points_a.append(point_a)
        points_b.append(point_b)
        if point_a.value is None or point_b.value is None:
            reason = point_a.skip_reason or point_b.skip_reason or SKIP_UNDEFINED
            points_rel.append(skip_point(ts, reason))
            continue
        points_rel.append(value_point(ts, point_a.value - point_b.value))
    return RelativeReturnResult(
        symbol_a=series_a.symbol,
        symbol_b=series_b.symbol,
        timeframe=series_a.timeframe,
        baseline_timestamp=baseline_ts,
        return_a=series_from_points(name="simple_return", series=series_a, points=points_a),
        return_b=series_from_points(name="simple_return", series=series_b, points=points_b),
        relative=IndicatorSeries(
            name="relative_return",
            symbol=pair_symbol,
            timeframe=series_a.timeframe,
            points=tuple(points_rel),
        ),
    )

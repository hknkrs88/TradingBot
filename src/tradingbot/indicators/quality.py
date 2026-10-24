"""Diagnostic summaries of indicator series. Does not recompute indicator values."""

from __future__ import annotations

from dataclasses import dataclass

from tradingbot.indicators.models import SKIP_REASONS, IndicatorSeries
from tradingbot.indicators.overlap import align_indicator_series


@dataclass(frozen=True)
class IndicatorQualityReport:
    """Point-level quality of one ``IndicatorSeries``. Values are not modified.

    ``valued_ratio`` is ``valued_count / point_count``. An empty series uses
    ``valued_ratio=0.0`` so the ratio never divides by zero.
    ``IndicatorPoint`` rejects NaN/inf values, so they cannot appear as valued.
    """

    name: str
    symbol: str
    timeframe: str
    point_count: int
    valued_count: int
    skip_counts: tuple[tuple[str, int], ...]
    first_timestamp: int | None
    last_timestamp: int | None
    first_valued_timestamp: int | None
    last_valued_timestamp: int | None
    valued_ratio: float


@dataclass(frozen=True)
class TimestampCoverageReport:
    """Coverage of two series using ``align_indicator_series`` (timestamp intersection).

    ``coverage_ratio`` is ``common_count / union_count`` where
    ``union_count = left_count + right_count - common_count``.
    Two empty series use ``coverage_ratio=1.0``. Timeframe mismatch raises
    ``ValueError``, same as ``align_indicator_series``.
    """

    left_symbol: str
    right_symbol: str
    timeframe: str
    left_count: int
    right_count: int
    common_count: int
    left_only_count: int
    right_only_count: int
    union_count: int
    coverage_ratio: float


def summarize_indicator_series(series: IndicatorSeries) -> IndicatorQualityReport:
    points = series.points
    valued = [item for item in points if item.value is not None]
    skip_tally = {reason: 0 for reason in sorted(SKIP_REASONS)}
    for item in points:
        if item.skip_reason is None:
            continue
        skip_tally[item.skip_reason] = skip_tally[item.skip_reason] + 1
    point_count = len(points)
    valued_count = len(valued)
    return IndicatorQualityReport(
        name=series.name,
        symbol=series.symbol,
        timeframe=series.timeframe,
        point_count=point_count,
        valued_count=valued_count,
        skip_counts=tuple((reason, skip_tally[reason]) for reason in sorted(SKIP_REASONS)),
        first_timestamp=points[0].timestamp if points else None,
        last_timestamp=points[-1].timestamp if points else None,
        first_valued_timestamp=valued[0].timestamp if valued else None,
        last_valued_timestamp=valued[-1].timestamp if valued else None,
        valued_ratio=(valued_count / point_count) if point_count else 0.0,
    )


def timestamp_coverage(left: IndicatorSeries, right: IndicatorSeries) -> TimestampCoverageReport:
    aligned = align_indicator_series(left, right)
    left_count = len(left.points)
    right_count = len(right.points)
    common_count = len(aligned.timestamps)
    union_count = left_count + right_count - common_count
    if union_count == 0:
        ratio = 1.0
    else:
        ratio = common_count / union_count
    return TimestampCoverageReport(
        left_symbol=left.symbol,
        right_symbol=right.symbol,
        timeframe=aligned.timeframe,
        left_count=left_count,
        right_count=right_count,
        common_count=common_count,
        left_only_count=len(aligned.left_only),
        right_only_count=len(aligned.right_only),
        union_count=union_count,
        coverage_ratio=ratio,
    )

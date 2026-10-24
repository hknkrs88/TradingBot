import pytest

from tradingbot.indicators import (
    SKIP_GAP,
    SKIP_REASONS,
    SKIP_UNDEFINED,
    SKIP_WARMUP,
    IndicatorPoint,
    IndicatorSeries,
    summarize_indicator_series,
    timestamp_coverage,
)

TF = 14_400_000


def _series(
    rows: list[tuple[int, float | None, str | None]],
    *,
    symbol: str = "AAA/USDT",
    name: str = "sma_2",
    timeframe: str = "4h",
) -> IndicatorSeries:
    points: list[IndicatorPoint] = []
    for ts, value, reason in rows:
        if value is None:
            points.append(IndicatorPoint(timestamp=ts, value=None, skip_reason=reason))
        else:
            points.append(IndicatorPoint(timestamp=ts, value=value, skip_reason=None))
    return IndicatorSeries(name=name, symbol=symbol, timeframe=timeframe, points=tuple(points))


def _skip_map(report) -> dict[str, int]:
    return dict(report.skip_counts)


def test_quality_all_valued() -> None:
    report = summarize_indicator_series(
        _series([(0, 1.0, None), (TF, 2.0, None), (2 * TF, 3.0, None)])
    )
    assert report.symbol == "AAA/USDT"
    assert report.timeframe == "4h"
    assert report.name == "sma_2"
    assert report.point_count == 3
    assert report.valued_count == 3
    assert report.valued_ratio == pytest.approx(1.0)
    assert report.first_timestamp == 0
    assert report.last_timestamp == 2 * TF
    assert report.first_valued_timestamp == 0
    assert report.last_valued_timestamp == 2 * TF
    assert sum(_skip_map(report).values()) == 0


def test_quality_multiple_skip_reasons_sum_to_skips() -> None:
    report = summarize_indicator_series(
        _series(
            [
                (0, None, SKIP_WARMUP),
                (TF, 1.0, None),
                (2 * TF, None, SKIP_GAP),
                (3 * TF, None, SKIP_UNDEFINED),
            ]
        )
    )
    counts = _skip_map(report)
    assert counts[SKIP_WARMUP] == 1
    assert counts[SKIP_GAP] == 1
    assert counts[SKIP_UNDEFINED] == 1
    assert report.valued_count == 1
    assert sum(counts.values()) == report.point_count - report.valued_count
    assert set(counts) == SKIP_REASONS
    assert report.valued_ratio == pytest.approx(0.25)
    assert report.first_valued_timestamp == TF
    assert report.last_valued_timestamp == TF


def test_quality_empty_series_ratio_is_zero() -> None:
    report = summarize_indicator_series(_series([]))
    assert report.point_count == 0
    assert report.valued_count == 0
    assert report.valued_ratio == 0.0
    assert report.first_timestamp is None
    assert report.last_valued_timestamp is None
    assert sum(_skip_map(report).values()) == 0


def test_quality_all_skipped() -> None:
    report = summarize_indicator_series(
        _series([(0, None, SKIP_WARMUP), (TF, None, SKIP_WARMUP)])
    )
    assert report.valued_count == 0
    assert report.valued_ratio == 0.0
    assert report.first_valued_timestamp is None
    assert _skip_map(report)[SKIP_WARMUP] == 2


def test_quality_single_valued_point() -> None:
    report = summarize_indicator_series(_series([(TF, 4.5, None)]))
    assert report.point_count == 1
    assert report.valued_ratio == 1.0
    assert report.first_timestamp == report.last_valued_timestamp == TF


def test_coverage_identical_timestamps() -> None:
    left = _series([(0, 1.0, None), (TF, 2.0, None)], symbol="AAA/USDT")
    right = _series([(0, 9.0, None), (TF, 8.0, None)], symbol="BBB/USDT")
    report = timestamp_coverage(left, right)
    assert report.left_count == 2
    assert report.right_count == 2
    assert report.common_count == 2
    assert report.left_only_count == 0
    assert report.right_only_count == 0
    assert report.union_count == 2
    assert report.coverage_ratio == pytest.approx(1.0)


def test_coverage_partial_and_different_lengths() -> None:
    left = _series([(0, 1.0, None), (TF, 2.0, None), (2 * TF, 3.0, None)], symbol="AAA/USDT")
    right = _series([(TF, 9.0, None)], symbol="BBB/USDT")
    report = timestamp_coverage(left, right)
    assert report.left_count == 3
    assert report.right_count == 1
    assert report.common_count == 1
    assert report.left_only_count == 2
    assert report.right_only_count == 0
    assert report.union_count == 3
    assert report.coverage_ratio == pytest.approx(1 / 3)


def test_coverage_no_overlap() -> None:
    left = _series([(0, 1.0, None)], symbol="AAA/USDT")
    right = _series([(TF, 1.0, None)], symbol="BBB/USDT")
    report = timestamp_coverage(left, right)
    assert report.common_count == 0
    assert report.union_count == 2
    assert report.coverage_ratio == pytest.approx(0.0)


def test_coverage_two_empty_series_ratio_is_one() -> None:
    report = timestamp_coverage(_series([], symbol="AAA/USDT"), _series([], symbol="BBB/USDT"))
    assert report.union_count == 0
    assert report.coverage_ratio == pytest.approx(1.0)


def test_coverage_timeframe_mismatch_raises() -> None:
    left = _series([(0, 1.0, None)], timeframe="4h")
    right = _series([(0, 1.0, None)], timeframe="1h")
    with pytest.raises(ValueError, match="timeframe"):
        timestamp_coverage(left, right)


def test_quality_and_coverage_are_deterministic() -> None:
    series = _series([(0, None, SKIP_WARMUP), (TF, 1.0, None)])
    other = _series([(TF, 2.0, None), (2 * TF, 3.0, None)], symbol="BBB/USDT")
    assert summarize_indicator_series(series) == summarize_indicator_series(series)
    assert timestamp_coverage(series, other) == timestamp_coverage(series, other)

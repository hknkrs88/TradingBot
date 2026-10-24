import math

import pytest

from tradingbot.indicators import (
    SKIP_REASONS,
    SKIP_WARMUP,
    IndicatorPoint,
    IndicatorSeries,
)


def test_package_import() -> None:
    import tradingbot.indicators as indicators

    assert indicators.IndicatorPoint is IndicatorPoint
    assert indicators.IndicatorSeries is IndicatorSeries
    assert SKIP_WARMUP in indicators.SKIP_REASONS


def test_point_requires_value_or_skip() -> None:
    with pytest.raises(ValueError, match="exactly one"):
        IndicatorPoint(timestamp=0, value=1.0, skip_reason=SKIP_WARMUP)
    with pytest.raises(ValueError, match="exactly one"):
        IndicatorPoint(timestamp=0, value=None, skip_reason=None)


def test_point_rejects_unknown_skip_and_non_finite_value() -> None:
    with pytest.raises(ValueError, match="unknown skip_reason"):
        IndicatorPoint(timestamp=0, value=None, skip_reason="look_ahead")
    with pytest.raises(ValueError, match="finite"):
        IndicatorPoint(timestamp=0, value=math.nan)
    with pytest.raises(ValueError, match="finite"):
        IndicatorPoint(timestamp=0, value=math.inf)


def test_valid_value_and_skip_points() -> None:
    valued = IndicatorPoint(timestamp=14_400_000, value=1.5)
    skipped = IndicatorPoint(timestamp=0, value=None, skip_reason=SKIP_WARMUP)
    assert valued.skip_reason is None
    assert skipped.value is None
    assert SKIP_WARMUP in SKIP_REASONS


def test_series_rejects_empty_identity_and_bad_timeframe() -> None:
    point = IndicatorPoint(timestamp=0, value=1.0)
    with pytest.raises(ValueError, match="name"):
        IndicatorSeries(name="", symbol="AAA/USDT", timeframe="4h", points=(point,))
    with pytest.raises(ValueError, match="symbol"):
        IndicatorSeries(name="sma", symbol="", timeframe="4h", points=(point,))
    with pytest.raises(ValueError):
        IndicatorSeries(name="sma", symbol="AAA/USDT", timeframe="1d", points=(point,))


def test_series_requires_sorted_unique_tuple() -> None:
    a = IndicatorPoint(timestamp=0, value=1.0)
    b = IndicatorPoint(timestamp=14_400_000, value=2.0)
    with pytest.raises(ValueError, match="tuple"):
        IndicatorSeries(name="sma", symbol="AAA/USDT", timeframe="4h", points=[a])  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="sorted"):
        IndicatorSeries(name="sma", symbol="AAA/USDT", timeframe="4h", points=(b, a))
    with pytest.raises(ValueError, match="duplicate"):
        IndicatorSeries(name="sma", symbol="AAA/USDT", timeframe="4h", points=(a, a))
    series = IndicatorSeries(
        name="sma",
        symbol="AAA/USDT",
        timeframe="4h",
        points=(a, b),
    )
    assert len(series.points) == 2

import math

import pytest

from tradingbot.data.ohlcv.models import Candle
from tradingbot.indicators import (
    SKIP_NON_FINITE,
    SKIP_UNDEFINED,
    SKIP_WARMUP,
    IndicatorPoint,
    IndicatorSeries,
    align_indicator_series,
    relative_return,
)
from tradingbot.strategy.models import CandleSeries

TF4 = 14_400_000
TF1 = 3_600_000


def _candle(ts: int, close: float, *, symbol: str = "AAA/USDT", timeframe: str = "4h") -> Candle:
    return Candle(timestamp=ts, open=close, high=close, low=close, close=close, volume=1.0)


def _price_series(
    symbol: str,
    pairs: list[tuple[int, float]],
    *,
    timeframe: str = "4h",
    tf_ms: int = TF4,
) -> CandleSeries:
    candles = tuple(_candle(ts, close) for ts, close in pairs)
    return CandleSeries.from_closed(
        symbol=symbol,
        timeframe=timeframe,
        candles=candles,
        now_ms=candles[-1].timestamp + tf_ms,
    )


def _points(rows: list[tuple[int, float | None, str | None]]) -> tuple[IndicatorPoint, ...]:
    out: list[IndicatorPoint] = []
    for ts, value, reason in rows:
        if value is None:
            out.append(IndicatorPoint(timestamp=ts, value=None, skip_reason=reason))
        else:
            out.append(IndicatorPoint(timestamp=ts, value=value, skip_reason=None))
    return tuple(out)


def _indicator(
    symbol: str,
    rows: list[tuple[int, float | None, str | None]],
    *,
    timeframe: str = "4h",
    name: str = "sma_2",
) -> IndicatorSeries:
    return IndicatorSeries(name=name, symbol=symbol, timeframe=timeframe, points=_points(rows))


def test_relative_return_on_shared_timestamps() -> None:
    a = _price_series("AAA/USDT", [(0, 10.0), (TF4, 12.0), (2 * TF4, 11.0)])
    b = _price_series("BBB/USDT", [(0, 20.0), (TF4, 22.0), (2 * TF4, 18.0)])
    result = relative_return(a, b)
    assert result.baseline_timestamp == 0
    assert result.symbol_a == "AAA/USDT"
    assert result.symbol_b == "BBB/USDT"
    assert result.timeframe == "4h"
    assert result.relative.symbol == "AAA/USDT|BBB/USDT"
    assert result.return_a.points[0].value == pytest.approx(0.0)
    assert result.return_a.points[1].value == pytest.approx(0.2)
    assert result.return_b.points[1].value == pytest.approx(0.1)
    assert result.relative.points[1].value == pytest.approx(0.1)
    assert result.relative.points[2].value == pytest.approx((11.0 / 10.0 - 1.0) - (18.0 / 20.0 - 1.0))


def test_relative_return_uses_intersection_only() -> None:
    a = _price_series("AAA/USDT", [(0, 10.0), (TF4, 12.0), (2 * TF4, 14.0)])
    b = _price_series("BBB/USDT", [(TF4, 20.0), (3 * TF4, 24.0)])
    result = relative_return(a, b)
    assert result.relative.points[0].timestamp == TF4
    assert len(result.relative.points) == 1
    assert result.baseline_timestamp == TF4
    aligned = align_indicator_series(
        _indicator("AAA/USDT", [(0, 1.0, None), (TF4, 2.0, None), (2 * TF4, 3.0, None)]),
        _indicator("BBB/USDT", [(TF4, 9.0, None), (3 * TF4, 8.0, None)]),
    )
    assert aligned.timestamps == (TF4,)
    assert aligned.left_only == (0, 2 * TF4)
    assert aligned.right_only == (3 * TF4,)


def test_align_empty_when_no_common_timestamp() -> None:
    aligned = align_indicator_series(
        _indicator("AAA/USDT", [(0, 1.0, None)]),
        _indicator("BBB/USDT", [(TF4, 1.0, None)]),
    )
    assert aligned.timestamps == ()
    assert relative_return(
        _price_series("AAA/USDT", [(0, 10.0)]),
        _price_series("BBB/USDT", [(TF4, 10.0)]),
    ).baseline_timestamp is None


def test_align_is_chronological_and_deterministic() -> None:
    left = _indicator("AAA/USDT", [(0, 1.0, None), (TF4, 2.0, None), (2 * TF4, 3.0, None)])
    right = _indicator("BBB/USDT", [(TF4, 9.0, None), (2 * TF4, 8.0, None)])
    first = align_indicator_series(left, right)
    second = align_indicator_series(left, right)
    assert first.timestamps == (TF4, 2 * TF4)
    assert first == second


def test_timeframe_mismatch_raises() -> None:
    left = _indicator("AAA/USDT", [(0, 1.0, None)], timeframe="4h")
    right = _indicator("BBB/USDT", [(0, 1.0, None)], timeframe="1h")
    with pytest.raises(ValueError, match="timeframe"):
        align_indicator_series(left, right)
    a = _price_series("AAA/USDT", [(0, 10.0)], timeframe="4h", tf_ms=TF4)
    b = CandleSeries.from_closed(
        symbol="BBB/USDT",
        timeframe="1h",
        candles=(_candle(0, 10.0),),
        now_ms=TF1,
    )
    with pytest.raises(ValueError, match="timeframe"):
        relative_return(a, b)


def test_zero_and_negative_baseline_is_undefined() -> None:
    zero = relative_return(
        _price_series("AAA/USDT", [(0, 0.0), (TF4, 2.0)]),
        _price_series("BBB/USDT", [(0, 5.0), (TF4, 6.0)]),
    )
    assert all(p.skip_reason == SKIP_UNDEFINED for p in zero.return_a.points)
    negative = relative_return(
        _price_series("AAA/USDT", [(0, -1.0), (TF4, 2.0)]),
        _price_series("BBB/USDT", [(0, 5.0), (TF4, 6.0)]),
    )
    assert negative.return_a.points[0].skip_reason == SKIP_UNDEFINED


def test_nan_close_and_indicator_skips_are_kept() -> None:
    nan_series = CandleSeries.from_closed(
        symbol="AAA/USDT",
        timeframe="4h",
        candles=(
            _candle(0, 10.0),
            Candle(timestamp=TF4, open=math.nan, high=math.nan, low=math.nan, close=math.nan, volume=1.0),
        ),
        now_ms=2 * TF4,
    )
    other = _price_series("BBB/USDT", [(0, 20.0), (TF4, 22.0)])
    result = relative_return(nan_series, other)
    assert result.return_a.points[1].skip_reason == SKIP_NON_FINITE
    assert result.relative.points[1].skip_reason == SKIP_NON_FINITE
    aligned = align_indicator_series(
        _indicator("AAA/USDT", [(0, None, SKIP_WARMUP), (TF4, 2.0, None)]),
        _indicator("BBB/USDT", [(0, 1.0, None), (TF4, 3.0, None)]),
    )
    assert aligned.left[0].skip_reason == SKIP_WARMUP
    assert aligned.right[0].value == pytest.approx(1.0)


def test_gap_on_one_side_is_not_filled() -> None:
    a = _price_series("AAA/USDT", [(0, 10.0), (2 * TF4, 12.0)])
    b = _price_series("BBB/USDT", [(0, 20.0), (TF4, 21.0), (2 * TF4, 22.0)])
    result = relative_return(a, b)
    assert [p.timestamp for p in result.relative.points] == [0, 2 * TF4]
    aligned = align_indicator_series(
        _indicator("AAA/USDT", [(0, 1.0, None), (2 * TF4, 2.0, None)]),
        _indicator("BBB/USDT", [(0, 1.0, None), (TF4, 1.5, None), (2 * TF4, 2.0, None)]),
    )
    assert aligned.right_only == (TF4,)


def test_relative_prefix_and_identity() -> None:
    a = _price_series("AAA/USDT", [(0, 10.0), (TF4, 12.0), (2 * TF4, 13.0)])
    b = _price_series("BBB/USDT", [(0, 20.0), (TF4, 21.0), (2 * TF4, 19.0)])
    full = relative_return(a, b)
    prefix_a = CandleSeries.from_closed(
        symbol="AAA/USDT",
        timeframe="4h",
        candles=a.candles[:2],
        now_ms=a.candles[1].timestamp + TF4,
    )
    prefix_b = CandleSeries.from_closed(
        symbol="BBB/USDT",
        timeframe="4h",
        candles=b.candles[:2],
        now_ms=b.candles[1].timestamp + TF4,
    )
    prefix = relative_return(prefix_a, prefix_b)
    assert prefix.relative.points[-1] == full.relative.points[1]
    assert relative_return(a, b) == full

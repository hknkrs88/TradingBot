"""SMA, EMA, and MACD on closed candle closes. No orders or scoring."""

from __future__ import annotations

from dataclasses import dataclass

from tradingbot.indicators.common import (
    SKIP_NON_FINITE,
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

MACD_FAST = 12
MACD_SLOW = 26
MACD_SIGNAL = 9


def sma(series: CandleSeries, period: int) -> IndicatorSeries:
    """Arithmetic mean of ``period`` consecutive closes. Does not fill gaps."""
    period = require_period(period)
    tf_ms = timeframe_ms(series.timeframe)
    points: list[IndicatorPoint] = []
    candles = series.candles
    for index, candle in enumerate(candles):
        if not close_is_finite(candle):
            points.append(skip_point(candle.timestamp, SKIP_NON_FINITE))
            continue
        if index + 1 < period:
            points.append(skip_point(candle.timestamp, SKIP_WARMUP))
            continue
        window = candles[index - period + 1 : index + 1]
        if not window_is_consecutive(window, tf_ms):
            points.append(skip_point(candle.timestamp, SKIP_WARMUP))
            continue
        if any(not close_is_finite(item) for item in window):
            points.append(skip_point(candle.timestamp, SKIP_NON_FINITE))
            continue
        mean = sum(item.close for item in window) / float(period)
        points.append(value_point(candle.timestamp, mean))
    return series_from_points(name=f"sma_{period}", series=series, points=points)


def ema(series: CandleSeries, period: int) -> IndicatorSeries:
    """EMA with α=2/(n+1), seeded by SMA of the first n consecutive closes of a run."""
    period = require_period(period)
    tf_ms = timeframe_ms(series.timeframe)
    alpha = 2.0 / (period + 1.0)
    points: list[IndicatorPoint] = []
    candles = series.candles
    run_len = 0
    previous_ema: float | None = None
    for index, candle in enumerate(candles):
        if not close_is_finite(candle):
            points.append(skip_point(candle.timestamp, SKIP_NON_FINITE))
            run_len = 0
            previous_ema = None
            continue
        attached = index > 0 and consecutive(candles[index - 1], candle, tf_ms) and run_len > 0
        if attached:
            run_len += 1
        else:
            run_len = 1
            previous_ema = None
        if run_len < period:
            points.append(skip_point(candle.timestamp, SKIP_WARMUP))
            continue
        if run_len == period:
            window = candles[index - period + 1 : index + 1]
            seed = sum(item.close for item in window) / float(period)
            previous_ema = seed
            points.append(value_point(candle.timestamp, seed))
            continue
        assert previous_ema is not None
        current = alpha * candle.close + (1.0 - alpha) * previous_ema
        previous_ema = current
        points.append(value_point(candle.timestamp, current))
    return series_from_points(name=f"ema_{period}", series=series, points=points)


@dataclass(frozen=True)
class MacdResult:
    macd: IndicatorSeries
    signal: IndicatorSeries
    histogram: IndicatorSeries


def _ema_on_values(
    points: tuple[IndicatorPoint, ...],
    period: int,
    *,
    name: str,
    symbol: str,
    timeframe: str,
) -> IndicatorSeries:
    """EMA over valued points only. Skips are not treated as zero."""
    period = require_period(period)
    tf_ms = timeframe_ms(timeframe)
    alpha = 2.0 / (period + 1.0)
    out: list[IndicatorPoint] = []
    run_len = 0
    previous_ema: float | None = None
    previous_ts: int | None = None
    seed: list[float] = []
    for point in points:
        if point.value is None:
            out.append(skip_point(point.timestamp, point.skip_reason or SKIP_WARMUP))
            run_len = 0
            previous_ema = None
            previous_ts = None
            seed = []
            continue
        attached = previous_ts is not None and point.timestamp - previous_ts == tf_ms and run_len > 0
        previous_ts = point.timestamp
        if attached:
            run_len += 1
            seed.append(point.value)
        else:
            run_len = 1
            previous_ema = None
            seed = [point.value]
        if run_len < period:
            out.append(skip_point(point.timestamp, SKIP_WARMUP))
            continue
        if run_len == period:
            previous_ema = sum(seed) / float(period)
            out.append(value_point(point.timestamp, previous_ema))
            continue
        assert previous_ema is not None
        current = alpha * point.value + (1.0 - alpha) * previous_ema
        previous_ema = current
        out.append(value_point(point.timestamp, current))
    return IndicatorSeries(name=name, symbol=symbol, timeframe=timeframe, points=tuple(out))


def macd(
    series: CandleSeries,
    *,
    fast: int = MACD_FAST,
    slow: int = MACD_SLOW,
    signal: int = MACD_SIGNAL,
) -> MacdResult:
    """MACD line = EMA(fast) − EMA(slow). Signal is EMA of valued MACD points."""
    fast = require_period(fast)
    slow = require_period(slow)
    signal = require_period(signal)
    if fast >= slow:
        raise ValueError("fast period must be < slow period")
    fast_line = ema(series, fast)
    slow_line = ema(series, slow)
    macd_points: list[IndicatorPoint] = []
    for fast_pt, slow_pt in zip(fast_line.points, slow_line.points, strict=True):
        ts = fast_pt.timestamp
        if fast_pt.value is None or slow_pt.value is None:
            reason = SKIP_WARMUP
            if fast_pt.skip_reason == SKIP_NON_FINITE or slow_pt.skip_reason == SKIP_NON_FINITE:
                reason = SKIP_NON_FINITE
            macd_points.append(skip_point(ts, reason))
            continue
        macd_points.append(value_point(ts, fast_pt.value - slow_pt.value))
    macd_series = series_from_points(name="macd", series=series, points=macd_points)
    signal_series = _ema_on_values(
        macd_series.points,
        signal,
        name="macd_signal",
        symbol=series.symbol,
        timeframe=series.timeframe,
    )
    hist_points: list[IndicatorPoint] = []
    for macd_pt, signal_pt in zip(macd_series.points, signal_series.points, strict=True):
        if macd_pt.value is None or signal_pt.value is None:
            reason = signal_pt.skip_reason or macd_pt.skip_reason or SKIP_WARMUP
            hist_points.append(skip_point(macd_pt.timestamp, reason))
            continue
        hist_points.append(value_point(macd_pt.timestamp, macd_pt.value - signal_pt.value))
    return MacdResult(
        macd=macd_series,
        signal=signal_series,
        histogram=series_from_points(name="macd_histogram", series=series, points=hist_points),
    )

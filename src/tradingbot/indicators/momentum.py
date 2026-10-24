"""ROC, RSI, and Stochastic RSI on closed candle closes. No orders or scoring."""

from __future__ import annotations

from dataclasses import dataclass

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
)
from tradingbot.indicators.models import IndicatorPoint, IndicatorSeries
from tradingbot.strategy.models import CandleSeries

RSI_PERIOD = 14
STOCH_RSI_WINDOW = 14
STOCH_RSI_K_SMOOTH = 3
STOCH_RSI_D_SMOOTH = 3


def roc(series: CandleSeries, period: int) -> IndicatorSeries:
    """``close[t] / close[t-period] - 1`` using consecutive bars only."""
    period = require_period(period)
    tf_ms = timeframe_ms(series.timeframe)
    points: list[IndicatorPoint] = []
    candles = series.candles
    for index, candle in enumerate(candles):
        if not close_is_finite(candle):
            points.append(skip_point(candle.timestamp, SKIP_NON_FINITE))
            continue
        if index < period:
            points.append(skip_point(candle.timestamp, SKIP_WARMUP))
            continue
        lookback = candles[index - period]
        span = candle.timestamp - lookback.timestamp
        if span != period * tf_ms:
            points.append(skip_point(candle.timestamp, SKIP_GAP))
            continue
        if not close_is_finite(lookback):
            points.append(skip_point(candle.timestamp, SKIP_NON_FINITE))
            continue
        if lookback.close == 0.0:
            points.append(skip_point(candle.timestamp, SKIP_UNDEFINED))
            continue
        ratio = candle.close / lookback.close - 1.0
        points.append(value_point(candle.timestamp, ratio))
    return series_from_points(name=f"roc_{period}", series=series, points=points)


def _rsi_from_averages(timestamp: int, avg_gain: float, avg_loss: float) -> IndicatorPoint:
    if avg_gain == 0.0 and avg_loss == 0.0:
        return skip_point(timestamp, SKIP_UNDEFINED)
    if avg_loss == 0.0:
        return value_point(timestamp, 100.0)
    if avg_gain == 0.0:
        return value_point(timestamp, 0.0)
    relative_strength = avg_gain / avg_loss
    return value_point(timestamp, 100.0 - 100.0 / (1.0 + relative_strength))


def rsi(series: CandleSeries, period: int = RSI_PERIOD) -> IndicatorSeries:
    """Wilder RSI. Seed averages with SMA of the first ``period`` gains/losses."""
    period = require_period(period)
    tf_ms = timeframe_ms(series.timeframe)
    points: list[IndicatorPoint] = []
    seed_gains: list[float] = []
    seed_losses: list[float] = []
    avg_gain: float | None = None
    avg_loss: float | None = None
    previous = None
    for candle in series.candles:
        if not close_is_finite(candle):
            points.append(skip_point(candle.timestamp, SKIP_NON_FINITE))
            previous = None
            seed_gains = []
            seed_losses = []
            avg_gain = None
            avg_loss = None
            continue
        attached = previous is not None and consecutive(previous, candle, tf_ms)
        if not attached:
            previous = candle
            seed_gains = []
            seed_losses = []
            avg_gain = None
            avg_loss = None
            points.append(skip_point(candle.timestamp, SKIP_WARMUP))
            continue
        delta = candle.close - previous.close
        previous = candle
        gain = delta if delta > 0.0 else 0.0
        loss = -delta if delta < 0.0 else 0.0
        if avg_gain is None or avg_loss is None:
            seed_gains.append(gain)
            seed_losses.append(loss)
            if len(seed_gains) < period:
                points.append(skip_point(candle.timestamp, SKIP_WARMUP))
                continue
            avg_gain = sum(seed_gains) / float(period)
            avg_loss = sum(seed_losses) / float(period)
        else:
            avg_gain = (avg_gain * (period - 1) + gain) / float(period)
            avg_loss = (avg_loss * (period - 1) + loss) / float(period)
        points.append(_rsi_from_averages(candle.timestamp, avg_gain, avg_loss))
    return series_from_points(name=f"rsi_{period}", series=series, points=points)


@dataclass(frozen=True)
class StochRsiResult:
    """Percent K and D on a 0-100 scale. Raw stochastic is computed on RSI, not close."""

    k: IndicatorSeries
    d: IndicatorSeries


def stoch_rsi(
    series: CandleSeries,
    *,
    rsi_period: int = RSI_PERIOD,
    window: int = STOCH_RSI_WINDOW,
    k_smooth: int = STOCH_RSI_K_SMOOTH,
    d_smooth: int = STOCH_RSI_D_SMOOTH,
) -> StochRsiResult:
    """Stochastic RSI: (RSI-min)/(max-min) over ``window``, then SMA of K and D."""
    rsi_period = require_period(rsi_period)
    window = require_period(window)
    k_smooth = require_period(k_smooth)
    d_smooth = require_period(d_smooth)
    tf_ms = timeframe_ms(series.timeframe)
    rsi_series = rsi(series, rsi_period)
    rsi_hist: list[IndicatorPoint] = []
    raw_hist: list[float] = []
    k_hist: list[float] = []
    k_points: list[IndicatorPoint] = []
    d_points: list[IndicatorPoint] = []
    for point in rsi_series.points:
        ts = point.timestamp
        if point.value is None:
            reason = point.skip_reason or SKIP_WARMUP
            k_points.append(skip_point(ts, reason))
            d_points.append(skip_point(ts, reason))
            rsi_hist = []
            raw_hist = []
            k_hist = []
            continue
        if rsi_hist and ts - rsi_hist[-1].timestamp != tf_ms:
            rsi_hist = []
            raw_hist = []
            k_hist = []
        rsi_hist.append(point)
        if len(rsi_hist) < window:
            k_points.append(skip_point(ts, SKIP_WARMUP))
            d_points.append(skip_point(ts, SKIP_WARMUP))
            raw_hist = []
            k_hist = []
            continue
        window_pts = rsi_hist[-window:]
        if window_pts[-1].timestamp - window_pts[0].timestamp != (window - 1) * tf_ms:
            k_points.append(skip_point(ts, SKIP_WARMUP))
            d_points.append(skip_point(ts, SKIP_WARMUP))
            raw_hist = []
            k_hist = []
            continue
        values = [item.value for item in window_pts]
        assert all(item is not None for item in values)
        low = min(values)
        high = max(values)
        if high == low:
            k_points.append(skip_point(ts, SKIP_UNDEFINED))
            d_points.append(skip_point(ts, SKIP_UNDEFINED))
            raw_hist = []
            k_hist = []
            continue
        raw = (point.value - low) / (high - low)
        raw_hist.append(raw)
        if len(raw_hist) < k_smooth:
            k_points.append(skip_point(ts, SKIP_WARMUP))
            d_points.append(skip_point(ts, SKIP_WARMUP))
            k_hist = []
            continue
        k_value = 100.0 * (sum(raw_hist[-k_smooth:]) / float(k_smooth))
        k_points.append(value_point(ts, k_value))
        k_hist.append(k_value)
        if len(k_hist) < d_smooth:
            d_points.append(skip_point(ts, SKIP_WARMUP))
            continue
        d_value = sum(k_hist[-d_smooth:]) / float(d_smooth)
        d_points.append(value_point(ts, d_value))
    return StochRsiResult(
        k=series_from_points(name="stoch_rsi_k", series=series, points=k_points),
        d=series_from_points(name="stoch_rsi_d", series=series, points=d_points),
    )

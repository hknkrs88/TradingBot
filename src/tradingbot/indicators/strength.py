"""ADX, +DI, and -DI. Wilder smoothing. No orders or scoring."""

from __future__ import annotations

from dataclasses import dataclass

from tradingbot.data.ohlcv.models import Candle
from tradingbot.indicators.common import (
    SKIP_NON_FINITE,
    SKIP_UNDEFINED,
    SKIP_WARMUP,
    consecutive,
    ohlc_is_finite,
    require_period,
    series_from_points,
    skip_point,
    timeframe_ms,
    value_point,
)
from tradingbot.indicators.models import IndicatorPoint, IndicatorSeries
from tradingbot.strategy.models import CandleSeries

ADX_PERIOD = 14


def true_range(candle: Candle, previous_close: float) -> float:
    return max(
        candle.high - candle.low,
        abs(candle.high - previous_close),
        abs(candle.low - previous_close),
    )


def directional_moves(candle: Candle, previous: Candle) -> tuple[float, float]:
    """Return (+DM, −DM). Equal or non-positive moves yield (0, 0)."""
    up_move = candle.high - previous.high
    down_move = previous.low - candle.low
    if up_move > down_move and up_move > 0.0:
        return up_move, 0.0
    if down_move > up_move and down_move > 0.0:
        return 0.0, down_move
    return 0.0, 0.0


def _invalid_ohlc(candle: Candle) -> bool:
    return candle.high < candle.low


@dataclass(frozen=True)
class AdxResult:
    adx: IndicatorSeries
    plus_di: IndicatorSeries
    minus_di: IndicatorSeries
    dx: IndicatorSeries


def adx(series: CandleSeries, period: int = ADX_PERIOD) -> AdxResult:
    """Wilder ADX. First DI after ``period`` TR samples; first ADX after ``period`` DX values."""
    period = require_period(period)
    tf_ms = timeframe_ms(series.timeframe)
    plus_points: list[IndicatorPoint] = []
    minus_points: list[IndicatorPoint] = []
    dx_points: list[IndicatorPoint] = []
    adx_points: list[IndicatorPoint] = []
    previous: Candle | None = None
    seed_tr: list[float] = []
    seed_plus: list[float] = []
    seed_minus: list[float] = []
    avg_tr: float | None = None
    avg_plus: float | None = None
    avg_minus: float | None = None
    dx_seed: list[float] = []
    adx_value: float | None = None

    def reset_run() -> None:
        nonlocal seed_tr, seed_plus, seed_minus, avg_tr, avg_plus, avg_minus, dx_seed, adx_value
        seed_tr = []
        seed_plus = []
        seed_minus = []
        avg_tr = None
        avg_plus = None
        avg_minus = None
        dx_seed = []
        adx_value = None

    def skip_all(timestamp: int, reason: str) -> None:
        plus_points.append(skip_point(timestamp, reason))
        minus_points.append(skip_point(timestamp, reason))
        dx_points.append(skip_point(timestamp, reason))
        adx_points.append(skip_point(timestamp, reason))

    def skip_adx(timestamp: int, reason: str) -> None:
        nonlocal dx_seed, adx_value
        dx_points.append(skip_point(timestamp, reason))
        adx_points.append(skip_point(timestamp, reason))
        dx_seed = []
        adx_value = None

    for candle in series.candles:
        ts = candle.timestamp
        if not ohlc_is_finite(candle):
            skip_all(ts, SKIP_NON_FINITE)
            previous = None
            reset_run()
            continue
        if _invalid_ohlc(candle):
            skip_all(ts, SKIP_UNDEFINED)
            previous = None
            reset_run()
            continue
        attached = previous is not None and consecutive(previous, candle, tf_ms)
        if not attached:
            previous = candle
            reset_run()
            skip_all(ts, SKIP_WARMUP)
            continue
        tr = true_range(candle, previous.close)
        plus_dm, minus_dm = directional_moves(candle, previous)
        previous = candle
        if avg_tr is None:
            seed_tr.append(tr)
            seed_plus.append(plus_dm)
            seed_minus.append(minus_dm)
            if len(seed_tr) < period:
                skip_all(ts, SKIP_WARMUP)
                continue
            avg_tr = sum(seed_tr) / float(period)
            avg_plus = sum(seed_plus) / float(period)
            avg_minus = sum(seed_minus) / float(period)
        else:
            avg_tr = (avg_tr * (period - 1) + tr) / float(period)
            avg_plus = (avg_plus * (period - 1) + plus_dm) / float(period)
            avg_minus = (avg_minus * (period - 1) + minus_dm) / float(period)
        if avg_tr == 0.0:
            plus_points.append(skip_point(ts, SKIP_UNDEFINED))
            minus_points.append(skip_point(ts, SKIP_UNDEFINED))
            skip_adx(ts, SKIP_UNDEFINED)
            continue
        plus_di = 100.0 * avg_plus / avg_tr
        minus_di = 100.0 * avg_minus / avg_tr
        plus_points.append(value_point(ts, plus_di))
        minus_points.append(value_point(ts, minus_di))
        di_sum = plus_di + minus_di
        if di_sum == 0.0:
            skip_adx(ts, SKIP_UNDEFINED)
            continue
        dx_value = 100.0 * abs(plus_di - minus_di) / di_sum
        dx_points.append(value_point(ts, dx_value))
        if adx_value is None:
            dx_seed.append(dx_value)
            if len(dx_seed) < period:
                adx_points.append(skip_point(ts, SKIP_WARMUP))
                continue
            adx_value = sum(dx_seed) / float(period)
            adx_points.append(value_point(ts, adx_value))
            continue
        adx_value = (adx_value * (period - 1) + dx_value) / float(period)
        adx_points.append(value_point(ts, adx_value))

    return AdxResult(
        adx=series_from_points(name=f"adx_{period}", series=series, points=adx_points),
        plus_di=series_from_points(name=f"plus_di_{period}", series=series, points=plus_points),
        minus_di=series_from_points(name=f"minus_di_{period}", series=series, points=minus_points),
        dx=series_from_points(name=f"dx_{period}", series=series, points=dx_points),
    )

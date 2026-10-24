"""G-I independent references and invariants. Does not change indicator code.

First valued index (0-based, consecutive finite bars):
- SMA/EMA(n): n-1
- ROC(n) / volume_roc(n): n
- RSI(n): n  (n diffs, n+1 prices)
- Stoch RSI defaults: %K at 29, %D at 31
- MACD: line at 25, signal/histogram at 33
- +DI/-DI/DX / ATR(n): n
- ADX(n): 2*n-1
- OBV: 1 (seed 0, first signed volume)
- BB(n): n-1
"""

from __future__ import annotations

import math

import pytest

from tradingbot.data.ohlcv.models import Candle
from tradingbot.indicators import (
    SKIP_UNDEFINED,
    SKIP_WARMUP,
    adx,
    atr,
    bollinger_bands,
    directional_moves,
    ema,
    macd,
    obv,
    relative_return,
    roc,
    rsi,
    sma,
    stoch_rsi,
    volume_roc,
    volume_sma,
)
from tradingbot.strategy.models import CandleSeries

TF = 14_400_000
SYMBOL = "AAA/USDT"


def _c(index: int, close: float, *, high: float | None = None, low: float | None = None, volume: float = 1.0, start: int = 0) -> Candle:
    h = close if high is None else high
    l = close if low is None else low
    return Candle(
        timestamp=(start + index) * TF,
        open=close,
        high=h,
        low=l,
        close=close,
        volume=volume,
    )


def _series(candles: tuple[Candle, ...]) -> CandleSeries:
    return CandleSeries.from_closed(
        symbol=SYMBOL,
        timeframe="4h",
        candles=candles,
        now_ms=candles[-1].timestamp + TF,
    )


def _closes(values: list[float], *, volume: float = 1.0) -> CandleSeries:
    return _series(tuple(_c(i, close, volume=volume) for i, close in enumerate(values)))


def test_sma_ema_independent_seed_and_update() -> None:
    series = _closes([10.0, 11.0, 12.0, 10.0, 13.0])
    sma3 = sma(series, 3)
    ema3 = ema(series, 3)
    assert sma3.points[2].value == pytest.approx((10.0 + 11.0 + 12.0) / 3.0)
    assert sma3.points[3].value == pytest.approx((11.0 + 12.0 + 10.0) / 3.0)
    assert sma3.points[4].value == pytest.approx((12.0 + 10.0 + 13.0) / 3.0)
    assert ema3.points[2].value == pytest.approx(11.0)
    assert ema3.points[3].value == pytest.approx(0.5 * 10.0 + 0.5 * 11.0)
    assert ema3.points[4].value == pytest.approx(0.5 * 13.0 + 0.5 * 10.5)


def test_roc_and_volume_roc_independent_ratios() -> None:
    rows = (
        _c(0, 10.0, volume=10.0),
        _c(1, 12.0, volume=12.0),
        _c(2, 9.0, volume=9.0),
        _c(3, 9.0, volume=9.0),
    )
    series = _series(rows)
    price = roc(series, 1)
    vol = volume_roc(series, 1)
    assert price.points[1].value == pytest.approx(12.0 / 10.0 - 1.0)
    assert price.points[2].value == pytest.approx(9.0 / 12.0 - 1.0)
    assert price.points[3].value == pytest.approx(0.0)
    assert vol.points[1].value == pytest.approx(12.0 / 10.0 - 1.0)
    assert vol.points[2].value == pytest.approx(9.0 / 12.0 - 1.0)
    assert vol.points[3].value == pytest.approx(0.0)


def test_rsi_wilder_independent_then_stoch_from_those_rsi_values() -> None:
    series = _closes([10.0, 11.0, 12.0, 10.0, 13.0])
    rsi_pts = rsi(series, 2)
    avg_gain_2 = (1.0 + 1.0) / 2.0
    avg_loss_2 = (0.0 + 0.0) / 2.0
    assert avg_loss_2 == 0.0
    assert rsi_pts.points[2].value == pytest.approx(100.0)
    avg_gain_3 = (avg_gain_2 * 1.0 + 0.0) / 2.0
    avg_loss_3 = (avg_loss_2 * 1.0 + 2.0) / 2.0
    rsi_3 = 100.0 - 100.0 / (1.0 + avg_gain_3 / avg_loss_3)
    assert rsi_pts.points[3].value == pytest.approx(rsi_3)
    avg_gain_4 = (avg_gain_3 * 1.0 + 3.0) / 2.0
    avg_loss_4 = (avg_loss_3 * 1.0 + 0.0) / 2.0
    rsi_4 = 100.0 - 100.0 / (1.0 + avg_gain_4 / avg_loss_4)
    assert rsi_pts.points[4].value == pytest.approx(rsi_4)
    stoch = stoch_rsi(series, rsi_period=2, window=2, k_smooth=1, d_smooth=1)
    raw_3 = (rsi_3 - min(100.0, rsi_3)) / (max(100.0, rsi_3) - min(100.0, rsi_3))
    raw_4 = (rsi_4 - min(rsi_3, rsi_4)) / (max(rsi_3, rsi_4) - min(rsi_3, rsi_4))
    assert stoch.k.points[3].value == pytest.approx(100.0 * raw_3)
    assert stoch.k.points[4].value == pytest.approx(100.0 * raw_4)


def test_macd_histogram_identity_independent_line() -> None:
    series = _closes([1.0, 2.0, 3.0, 4.0, 5.0, 6.0])
    fast_seed = (1.0 + 2.0) / 2.0
    fast_2 = (2.0 / 3.0) * 3.0 + (1.0 / 3.0) * fast_seed
    slow_2 = (1.0 + 2.0 + 3.0) / 3.0
    expected_macd_2 = fast_2 - slow_2
    result = macd(series, fast=2, slow=3, signal=2)
    assert result.macd.points[2].value == pytest.approx(expected_macd_2)
    for macd_pt, signal_pt, hist_pt in zip(
        result.macd.points, result.signal.points, result.histogram.points, strict=True
    ):
        if macd_pt.value is not None and signal_pt.value is not None:
            assert hist_pt.value == pytest.approx(macd_pt.value - signal_pt.value)


def test_tr_dm_di_dx_atr_independent() -> None:
    prev = _c(0, 9.0, high=10.0, low=8.0)
    bar = _c(1, 11.0, high=12.0, low=9.0)
    nxt = _c(2, 12.0, high=13.0, low=10.0)
    tr1 = max(12.0 - 9.0, abs(12.0 - 9.0), abs(9.0 - 9.0))
    tr2 = max(13.0 - 10.0, abs(13.0 - 11.0), abs(10.0 - 11.0))
    assert tr1 == pytest.approx(3.0)
    assert tr2 == pytest.approx(3.0)
    assert directional_moves(bar, prev) == (2.0, 0.0)
    assert directional_moves(nxt, bar) == (1.0, 0.0)
    series = _series((prev, bar, nxt, _c(3, 13.0, high=14.0, low=11.0)))
    plus_di = 100.0 * ((2.0 + 1.0) / 2.0) / ((tr1 + tr2) / 2.0)
    result = adx(series, 2)
    assert result.plus_di.points[2].value == pytest.approx(plus_di)
    assert result.minus_di.points[2].value == pytest.approx(0.0)
    assert result.dx.points[2].value == pytest.approx(100.0)
    atr_pts = atr(series, 2)
    assert atr_pts.points[2].value == pytest.approx((tr1 + tr2) / 2.0)
    tr3 = max(14.0 - 11.0, abs(14.0 - 12.0), abs(11.0 - 12.0))
    assert atr_pts.points[3].value == pytest.approx((((tr1 + tr2) / 2.0) * 1.0 + tr3) / 2.0)


def test_bollinger_independent_population_width() -> None:
    series = _closes([2.0, 4.0, 6.0])
    mean = 4.0
    stdev = math.sqrt(((2.0 - 4.0) ** 2 + (4.0 - 4.0) ** 2 + (6.0 - 4.0) ** 2) / 3.0)
    bands = bollinger_bands(series, period=3, std_multiplier=2.0)
    assert bands.middle.points[2].value == pytest.approx(mean)
    assert bands.upper.points[2].value == pytest.approx(mean + 2.0 * stdev)
    assert bands.lower.points[2].value == pytest.approx(mean - 2.0 * stdev)
    assert bands.width.points[2].value == pytest.approx((4.0 * stdev) / mean)


def test_obv_independent_signed_base_volume() -> None:
    series = _series(
        (
            _c(0, 10.0, volume=5.0),
            _c(1, 11.0, volume=2.0),
            _c(2, 9.0, volume=3.0),
            _c(3, 9.0, volume=8.0),
        )
    )
    pts = obv(series)
    assert pts.points[0].skip_reason == SKIP_WARMUP
    assert pts.points[1].value == pytest.approx(0.0 + 2.0)
    assert pts.points[2].value == pytest.approx(2.0 - 3.0)
    assert pts.points[3].value == pytest.approx(-1.0)


def test_relative_return_shares_first_common_baseline() -> None:
    a = CandleSeries.from_closed(
        symbol="AAA/USDT",
        timeframe="4h",
        candles=(_c(0, 10.0), _c(1, 12.0)),
        now_ms=2 * TF,
    )
    b = CandleSeries.from_closed(
        symbol="BBB/USDT",
        timeframe="4h",
        candles=(_c(0, 20.0), _c(1, 22.0)),
        now_ms=2 * TF,
    )
    rel = relative_return(a, b)
    assert rel.baseline_timestamp == 0
    assert rel.return_a.points[1].value == pytest.approx(12.0 / 10.0 - 1.0)
    assert rel.return_b.points[1].value == pytest.approx(22.0 / 20.0 - 1.0)
    assert rel.relative.points[1].value == pytest.approx((12.0 / 10.0 - 1.0) - (22.0 / 20.0 - 1.0))


def test_invariants_bands_rsi_stoch_di_atr() -> None:
    candles = tuple(
        _c(i, 50.0 + 4.0 * math.sin(i / 2.0), high=52.0 + 4.0 * math.sin(i / 2.0), low=48.0 + 4.0 * math.sin(i / 2.0), volume=10.0 + i)
        for i in range(16)
    )
    series = _series(candles)
    bands = bollinger_bands(series, period=5, std_multiplier=2.0)
    for mid, up, low in zip(bands.middle.points, bands.upper.points, bands.lower.points, strict=True):
        if mid.value is None or up.value is None or low.value is None:
            continue
        assert low.value <= mid.value <= up.value
    rsi_pts = rsi(series, 3)
    for point in rsi_pts.points:
        if point.value is not None:
            assert 0.0 <= point.value <= 100.0
    stoch = stoch_rsi(series, rsi_period=3, window=3, k_smooth=2, d_smooth=2)
    for point in (*stoch.k.points, *stoch.d.points):
        if point.value is not None:
            assert 0.0 <= point.value <= 100.0
    di = adx(series, 3)
    for point in (*di.plus_di.points, *di.minus_di.points):
        if point.value is not None:
            assert point.value >= 0.0
    for point in atr(series, 3).points:
        if point.value is not None:
            assert point.value >= 0.0
    macd_res = macd(series, fast=2, slow=4, signal=2)
    for macd_pt, signal_pt, hist_pt in zip(
        macd_res.macd.points, macd_res.signal.points, macd_res.histogram.points, strict=True
    ):
        if macd_pt.value is not None and signal_pt.value is not None:
            assert hist_pt.value == pytest.approx(macd_pt.value - signal_pt.value)


def test_constant_price_skip_and_zero_atr_width() -> None:
    series = _closes([5.0, 5.0, 5.0, 5.0, 5.0])
    assert rsi(series, 3).points[3].skip_reason == SKIP_UNDEFINED
    assert atr(_series(tuple(_c(i, 5.0, high=5.0, low=5.0) for i in range(5))), 1).points[1].value == pytest.approx(0.0)
    width = bollinger_bands(series, period=3, std_multiplier=2.0).width.points[3]
    assert width.value == pytest.approx(0.0)


def test_prefix_determinism_and_gap_warmup() -> None:
    full = _closes([10.0, 11.0, 9.0, 12.0, 8.0, 13.0, 7.0, 14.0])
    assert sma(full, 3) == sma(full, 3)
    prefix = _series(full.candles[:5])
    assert sma(prefix, 3).points[-1] == sma(full, 3).points[4]
    assert ema(prefix, 3).points[-1] == ema(full, 3).points[4]
    assert rsi(prefix, 3).points[-1] == rsi(full, 3).points[4]
    gapped = _series(
        tuple(_c(i, 10.0 + i) for i in range(3)) + tuple(_c(i, 20.0 + i, start=8) for i in range(4))
    )
    sma_g = sma(gapped, 3)
    assert sma_g.points[2].value is not None
    assert sma_g.points[3].skip_reason == SKIP_WARMUP
    assert volume_sma(gapped, 3).points[3].skip_reason == SKIP_WARMUP
    assert obv(gapped).points[3].skip_reason == SKIP_WARMUP

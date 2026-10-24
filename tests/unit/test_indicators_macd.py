import math

import pytest

from tradingbot.data.ohlcv.models import Candle
from tradingbot.indicators import (
    MACD_FAST,
    MACD_SIGNAL,
    MACD_SLOW,
    SKIP_NON_FINITE,
    SKIP_WARMUP,
    ema,
    macd,
)
from tradingbot.strategy.models import CandleSeries

TF = 14_400_000
SYMBOL = "AAA/USDT"


def _bar(index: int, close: float) -> Candle:
    return Candle(
        timestamp=index * TF,
        open=close,
        high=close,
        low=close,
        close=close,
        volume=1.0,
    )


def _series(closes: list[float]) -> CandleSeries:
    candles = tuple(_bar(i, close) for i, close in enumerate(closes))
    return CandleSeries.from_closed(
        symbol=SYMBOL,
        timeframe="4h",
        candles=candles,
        now_ms=candles[-1].timestamp + TF,
    )


def test_macd_line_is_fast_minus_slow_ema() -> None:
    series = _series([1.0, 2.0, 3.0, 4.0, 5.0, 6.0])
    result = macd(series, fast=2, slow=3, signal=2)
    fast_line = ema(series, 2)
    slow_line = ema(series, 3)
    assert result.macd.points[2].value == pytest.approx(
        fast_line.points[2].value - slow_line.points[2].value
    )
    assert result.macd.points[2].value == pytest.approx(0.5)
    assert result.macd.points[3].value == pytest.approx(0.5)


def test_macd_signal_warmup_uses_valued_macd_only() -> None:
    result = macd(_series([1.0, 2.0, 3.0, 4.0, 5.0, 6.0]), fast=2, slow=3, signal=2)
    assert result.macd.points[1].skip_reason == SKIP_WARMUP
    assert result.signal.points[2].skip_reason == SKIP_WARMUP
    assert result.signal.points[3].value == pytest.approx(0.5)
    assert result.histogram.points[3].value == pytest.approx(0.0)


def test_macd_default_first_valid_indices() -> None:
    closes = [100.0 + i * 0.5 for i in range(40)]
    result = macd(_series(closes))
    first_macd = MACD_SLOW - 1
    first_signal = first_macd + MACD_SIGNAL - 1
    assert first_macd == 25
    assert first_signal == 33
    assert result.macd.points[first_macd - 1].skip_reason == SKIP_WARMUP
    assert result.macd.points[first_macd].value is not None
    assert result.signal.points[first_signal - 1].skip_reason == SKIP_WARMUP
    assert result.signal.points[first_signal].value is not None
    assert result.histogram.points[first_signal].value is not None


def test_macd_histogram_is_macd_minus_signal() -> None:
    result = macd(_series([1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 8.0]), fast=2, slow=3, signal=2)
    for macd_pt, signal_pt, hist_pt in zip(
        result.macd.points, result.signal.points, result.histogram.points, strict=True
    ):
        if macd_pt.value is not None and signal_pt.value is not None:
            assert hist_pt.value == pytest.approx(macd_pt.value - signal_pt.value)


def test_macd_rejects_fast_not_less_than_slow() -> None:
    with pytest.raises(ValueError, match="fast"):
        macd(_series([1.0, 2.0, 3.0]), fast=12, slow=12, signal=9)


def test_macd_nan_is_non_finite_and_prefix_stable() -> None:
    candles = tuple(_bar(i, float(i + 1)) for i in range(8))
    candles = candles[:3] + (
        Candle(timestamp=3 * TF, open=math.nan, high=math.nan, low=math.nan, close=math.nan, volume=1.0),
    ) + candles[4:]
    series = CandleSeries.from_closed(
        symbol=SYMBOL, timeframe="4h", candles=candles, now_ms=candles[-1].timestamp + TF
    )
    result = macd(series, fast=2, slow=3, signal=2)
    assert result.macd.points[3].skip_reason == SKIP_NON_FINITE
    full = macd(_series([1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0]), fast=2, slow=3, signal=2)
    prefix = _series([1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0])
    prefix = CandleSeries.from_closed(
        symbol=SYMBOL,
        timeframe="4h",
        candles=prefix.candles[:5],
        now_ms=prefix.candles[4].timestamp + TF,
    )
    assert macd(prefix, fast=2, slow=3, signal=2).macd.points[-1] == full.macd.points[4]

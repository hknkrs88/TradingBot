import math

import pytest

from tradingbot.data.ohlcv.models import Candle
from tradingbot.indicators import SKIP_GAP, SKIP_NON_FINITE, SKIP_UNDEFINED, SKIP_WARMUP, roc
from tradingbot.selection.rank_features import rank_features_4h
from tradingbot.strategy.models import CandleSeries

TF = 14_400_000
SYMBOL = "AAA/USDT"


def _bar(index: int, close: float, *, start: int = 0) -> Candle:
    ts = (start + index) * TF
    return Candle(timestamp=ts, open=close, high=close, low=close, close=close, volume=1.0)


def _series(closes: list[float], *, start: int = 0) -> CandleSeries:
    candles = tuple(_bar(i, close, start=start) for i, close in enumerate(closes))
    last = candles[-1].timestamp
    return CandleSeries.from_closed(
        symbol=SYMBOL,
        timeframe="4h",
        candles=candles,
        now_ms=last + TF,
    )


def test_roc_positive_negative_and_zero() -> None:
    result = roc(_series([10.0, 12.0, 9.0, 9.0]), 1)
    assert result.points[0].skip_reason == SKIP_WARMUP
    assert result.points[1].value == pytest.approx(0.2)
    assert result.points[2].value == pytest.approx(-0.25)
    assert result.points[3].value == pytest.approx(0.0)
    assert result.name == "roc_1"


def test_roc_zero_denominator_is_undefined() -> None:
    result = roc(_series([0.0, 1.0, 2.0]), 1)
    assert result.points[1].skip_reason == SKIP_UNDEFINED
    assert result.points[2].value == pytest.approx(1.0)


def test_roc_warmup_until_lookback_exists() -> None:
    result = roc(_series([1.0, 2.0, 3.0]), 3)
    assert all(p.skip_reason == SKIP_WARMUP for p in result.points)


def test_roc_does_not_cross_gap() -> None:
    candles = (
        _bar(0, 10.0),
        _bar(1, 12.0),
        _bar(2, 8.0),
        _bar(0, 20.0, start=5),
        _bar(1, 22.0, start=5),
        _bar(2, 24.0, start=5),
    )
    series = CandleSeries.from_closed(
        symbol=SYMBOL, timeframe="4h", candles=candles, now_ms=candles[-1].timestamp + TF
    )
    result = roc(series, 2)
    assert result.points[2].value == pytest.approx(8.0 / 10.0 - 1.0)
    assert result.points[3].skip_reason == SKIP_GAP
    assert result.points[4].skip_reason == SKIP_GAP
    assert result.points[5].value == pytest.approx(24.0 / 20.0 - 1.0)


def test_roc_nan_and_inf_are_non_finite() -> None:
    candles = (
        _bar(0, 1.0),
        _bar(1, math.nan),
        _bar(2, math.inf),
        _bar(3, 2.0),
    )
    series = CandleSeries.from_closed(
        symbol=SYMBOL, timeframe="4h", candles=candles, now_ms=candles[-1].timestamp + TF
    )
    result = roc(series, 1)
    assert result.points[1].skip_reason == SKIP_NON_FINITE
    assert result.points[2].skip_reason == SKIP_NON_FINITE
    assert result.points[3].skip_reason == SKIP_NON_FINITE


def test_roc_prefix_matches_full_series() -> None:
    full = _series([10.0, 11.0, 9.0, 12.0, 8.0])
    full_roc = roc(full, 2)
    for end in range(1, len(full.candles) + 1):
        prefix = CandleSeries.from_closed(
            symbol=SYMBOL,
            timeframe="4h",
            candles=full.candles[:end],
            now_ms=full.candles[end - 1].timestamp + TF,
        )
        assert roc(prefix, 2).points[-1] == full_roc.points[end - 1]


def test_roc6_ratio_matches_rank_features_percent() -> None:
    candles = tuple(_bar(index, 50.0 + index) for index in range(50))
    series = CandleSeries.from_closed(
        symbol="BTC/USDT",
        timeframe="4h",
        candles=candles,
        now_ms=candles[-1].timestamp + TF,
    )
    features = rank_features_4h(series)
    point = roc(series, 6).points[-1]
    assert point.value is not None
    assert point.value * 100.0 == pytest.approx(features.roc6_pct)
    assert point.value == pytest.approx(99.0 / 93.0 - 1.0)


def test_roc_rejects_invalid_period() -> None:
    series = _series([1.0, 2.0, 3.0])
    with pytest.raises(ValueError, match="period"):
        roc(series, 0)

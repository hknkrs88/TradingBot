import math

import pytest

from tradingbot.data.ohlcv.models import Candle
from tradingbot.indicators import SKIP_NON_FINITE, SKIP_WARMUP, ema, sma
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


def test_sma_known_sequence() -> None:
    result = sma(_series([1.0, 2.0, 3.0, 4.0, 5.0]), 3)
    assert [p.skip_reason for p in result.points[:2]] == [SKIP_WARMUP, SKIP_WARMUP]
    assert result.points[2].value == pytest.approx(2.0)
    assert result.points[3].value == pytest.approx(3.0)
    assert result.points[4].value == pytest.approx(4.0)
    assert result.name == "sma_3"


def test_ema_known_sequence_seeded_by_sma() -> None:
    result = ema(_series([1.0, 2.0, 3.0, 4.0, 5.0]), 3)
    assert result.points[0].skip_reason == SKIP_WARMUP
    assert result.points[1].skip_reason == SKIP_WARMUP
    assert result.points[2].value == pytest.approx(2.0)
    assert result.points[3].value == pytest.approx(3.0)
    assert result.points[4].value == pytest.approx(4.0)
    assert result.name == "ema_3"


def test_sma_period_one_is_close() -> None:
    result = sma(_series([7.0, 8.0]), 1)
    assert [p.value for p in result.points] == [7.0, 8.0]


def test_insufficient_history_is_warmup() -> None:
    result = sma(_series([1.0, 2.0]), 5)
    assert all(p.skip_reason == SKIP_WARMUP for p in result.points)
    assert ema(_series([1.0, 2.0]), 5).points[0].skip_reason == SKIP_WARMUP


def test_period_must_be_positive_int() -> None:
    series = _series([1.0, 2.0, 3.0])
    for period in (0, -1, True, 1.5):
        with pytest.raises(ValueError, match="period"):
            sma(series, period)  # type: ignore[arg-type]


def test_gap_restarts_sma_on_post_gap_window_only() -> None:
    pre = [_bar(i, float(i + 1)) for i in range(3)]
    post = [_bar(i, 10.0 + i, start=5) for i in range(3)]
    candles = tuple(pre + post)
    series = CandleSeries.from_closed(
        symbol=SYMBOL,
        timeframe="4h",
        candles=candles,
        now_ms=candles[-1].timestamp + TF,
    )
    result = sma(series, 3)
    assert result.points[2].value == pytest.approx(2.0)
    assert result.points[3].skip_reason == SKIP_WARMUP
    assert result.points[4].skip_reason == SKIP_WARMUP
    assert result.points[5].value == pytest.approx((10.0 + 11.0 + 12.0) / 3.0)


def test_gap_reseeds_ema() -> None:
    pre = [_bar(i, float(i + 1)) for i in range(3)]
    post = [_bar(i, 10.0 + i, start=5) for i in range(4)]
    candles = tuple(pre + post)
    series = CandleSeries.from_closed(
        symbol=SYMBOL,
        timeframe="4h",
        candles=candles,
        now_ms=candles[-1].timestamp + TF,
    )
    result = ema(series, 3)
    assert result.points[2].value == pytest.approx(2.0)
    assert result.points[3].skip_reason == SKIP_WARMUP
    assert result.points[4].skip_reason == SKIP_WARMUP
    assert result.points[5].value == pytest.approx((10.0 + 11.0 + 12.0) / 3.0)
    assert result.points[6].value == pytest.approx(0.5 * 13.0 + 0.5 * 11.0)


def test_nan_and_inf_closes_are_non_finite() -> None:
    candles = (
        _bar(0, 1.0),
        _bar(1, math.nan),
        _bar(2, math.inf),
        _bar(3, 2.0),
        _bar(4, 3.0),
        _bar(5, 4.0),
    )
    series = CandleSeries.from_closed(
        symbol=SYMBOL, timeframe="4h", candles=candles, now_ms=candles[-1].timestamp + TF
    )
    sma_result = sma(series, 3)
    assert sma_result.points[1].skip_reason == SKIP_NON_FINITE
    assert sma_result.points[2].skip_reason == SKIP_NON_FINITE
    assert sma_result.points[5].value == pytest.approx(3.0)
    ema_result = ema(series, 3)
    assert ema_result.points[1].skip_reason == SKIP_NON_FINITE
    assert ema_result.points[5].value == pytest.approx(3.0)


def test_prefix_matches_full_series() -> None:
    full = _series([1.0, 2.0, 3.0, 4.0, 5.0, 6.0])
    full_sma = sma(full, 3)
    full_ema = ema(full, 3)
    for end in range(1, len(full.candles) + 1):
        prefix = CandleSeries.from_closed(
            symbol=SYMBOL,
            timeframe="4h",
            candles=full.candles[:end],
            now_ms=full.candles[end - 1].timestamp + TF,
        )
        assert sma(prefix, 3).points[-1] == full_sma.points[end - 1]
        assert ema(prefix, 3).points[-1] == full_ema.points[end - 1]


def test_sma_matches_rank_features_on_same_window() -> None:
    candles = tuple(_bar(index, 50.0 + index) for index in range(50))
    series = CandleSeries.from_closed(
        symbol="BTC/USDT",
        timeframe="4h",
        candles=candles,
        now_ms=candles[-1].timestamp + TF,
    )
    features = rank_features_4h(series)
    assert sma(series, 20).points[-1].value == pytest.approx(features.sma20)
    assert sma(series, 50).points[-1].value == pytest.approx(features.sma50)
    assert ema(series, 20).points[-1].value is not None

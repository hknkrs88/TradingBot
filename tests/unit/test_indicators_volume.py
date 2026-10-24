import math

import pytest

from tradingbot.data.ohlcv.models import Candle
from tradingbot.indicators import (
    SKIP_GAP,
    SKIP_NON_FINITE,
    SKIP_UNDEFINED,
    SKIP_WARMUP,
    obv,
    roc,
    volume_roc,
    volume_sma,
)
from tradingbot.strategy.models import CandleSeries

TF = 14_400_000
SYMBOL = "AAA/USDT"


def _bar(index: int, *, close: float, base_volume: float, start: int = 0) -> Candle:
    return Candle(
        timestamp=(start + index) * TF,
        open=close,
        high=close,
        low=close,
        close=close,
        volume=base_volume,
    )


def _series(rows: list[tuple[float, float]], *, start: int = 0) -> CandleSeries:
    candles = tuple(_bar(i, close=close, base_volume=base_volume, start=start) for i, (close, base_volume) in enumerate(rows))
    return CandleSeries.from_closed(
        symbol=SYMBOL,
        timeframe="4h",
        candles=candles,
        now_ms=candles[-1].timestamp + TF,
    )


def test_volume_sma_uses_base_volume_not_quote_proxy() -> None:
    series = _series([(10.0, 2.0), (20.0, 4.0), (40.0, 6.0)])
    result = volume_sma(series, 3)
    assert result.points[2].value == pytest.approx(4.0)
    quote_proxy = [(c.close * c.volume) for c in series.candles]
    assert result.points[2].value != pytest.approx(sum(quote_proxy) / 3.0)
    assert result.name == "volume_sma_3"


def test_volume_sma_zero_volume_is_valid() -> None:
    result = volume_sma(_series([(1.0, 0.0), (1.0, 0.0), (1.0, 2.0)]), 3)
    assert result.points[2].value == pytest.approx(2.0 / 3.0)


def test_volume_sma_negative_and_non_finite_base_volume() -> None:
    series = CandleSeries.from_closed(
        symbol=SYMBOL,
        timeframe="4h",
        candles=(
            _bar(0, close=1.0, base_volume=1.0),
            _bar(1, close=1.0, base_volume=-2.0),
            _bar(2, close=1.0, base_volume=math.nan),
        ),
        now_ms=3 * TF,
    )
    result = volume_sma(series, 1)
    assert result.points[1].skip_reason == SKIP_UNDEFINED
    assert result.points[2].skip_reason == SKIP_NON_FINITE


def test_obv_up_down_unchanged_and_start() -> None:
    result = obv(_series([(10.0, 5.0), (11.0, 2.0), (9.0, 3.0), (9.0, 8.0)]))
    assert result.points[0].skip_reason == SKIP_WARMUP
    assert result.points[1].value == pytest.approx(2.0)
    assert result.points[2].value == pytest.approx(-1.0)
    assert result.points[3].value == pytest.approx(-1.0)


def test_obv_gap_does_not_carry_total() -> None:
    pre = _series([(10.0, 5.0), (12.0, 4.0)])
    post = _series([(20.0, 7.0), (21.0, 1.0)], start=8)
    candles = pre.candles + post.candles
    series = CandleSeries.from_closed(
        symbol=SYMBOL, timeframe="4h", candles=candles, now_ms=candles[-1].timestamp + TF
    )
    result = obv(series)
    assert result.points[1].value == pytest.approx(4.0)
    assert result.points[2].skip_reason == SKIP_WARMUP
    assert result.points[3].value == pytest.approx(1.0)


def test_volume_roc_ratio_and_zero_denominator() -> None:
    result = volume_roc(_series([(1.0, 10.0), (1.0, 12.0), (1.0, 9.0), (1.0, 0.0), (1.0, 5.0)]), 1)
    assert result.points[0].skip_reason == SKIP_WARMUP
    assert result.points[1].value == pytest.approx(0.2)
    assert result.points[2].value == pytest.approx(-0.25)
    assert result.points[3].value == pytest.approx(-1.0)
    assert result.points[4].skip_reason == SKIP_UNDEFINED
    assert result.name == "volume_roc_1"
    assert roc(_series([(10.0, 1.0), (12.0, 1.0)]), 1).name == "roc_1"


def test_volume_roc_does_not_cross_gap() -> None:
    candles = (
        _bar(0, close=1.0, base_volume=10.0),
        _bar(1, close=1.0, base_volume=20.0),
        _bar(0, close=1.0, base_volume=40.0, start=5),
        _bar(1, close=1.0, base_volume=80.0, start=5),
    )
    series = CandleSeries.from_closed(
        symbol=SYMBOL, timeframe="4h", candles=candles, now_ms=candles[-1].timestamp + TF
    )
    result = volume_roc(series, 1)
    assert result.points[1].value == pytest.approx(1.0)
    assert result.points[2].skip_reason == SKIP_GAP
    assert result.points[3].value == pytest.approx(1.0)


def test_volume_indicators_prefix_and_warmup() -> None:
    rows = [(10.0, 2.0), (11.0, 4.0), (9.0, 6.0), (12.0, 8.0)]
    full = _series(rows)
    vsma = volume_sma(full, 3)
    assert vsma.points[1].skip_reason == SKIP_WARMUP
    prefix = CandleSeries.from_closed(
        symbol=SYMBOL, timeframe="4h", candles=full.candles[:3], now_ms=full.candles[2].timestamp + TF
    )
    assert volume_sma(prefix, 3).points[-1] == vsma.points[2]
    assert obv(prefix).points[-1] == obv(full).points[2]
    assert volume_roc(prefix, 1).points[-1] == volume_roc(full, 1).points[2]

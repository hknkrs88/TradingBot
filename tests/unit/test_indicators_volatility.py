import math

import pytest

from tradingbot.data.ohlcv.models import Candle
from tradingbot.indicators import (
    ATR_PERIOD,
    BB_PERIOD,
    SKIP_NON_FINITE,
    SKIP_UNDEFINED,
    SKIP_WARMUP,
    atr,
    bollinger_bands,
    population_stdev,
    sma,
    true_range,
)
from tradingbot.strategy.models import CandleSeries

TF = 14_400_000
SYMBOL = "AAA/USDT"


def _hlc(index: int, high: float, low: float, close: float, *, start: int = 0) -> Candle:
    return Candle(
        timestamp=(start + index) * TF,
        open=close,
        high=high,
        low=low,
        close=close,
        volume=1.0,
    )


def _close_series(closes: list[float]) -> CandleSeries:
    candles = tuple(_hlc(i, close, close, close) for i, close in enumerate(closes))
    return CandleSeries.from_closed(
        symbol=SYMBOL,
        timeframe="4h",
        candles=candles,
        now_ms=candles[-1].timestamp + TF,
    )


def _ohlc_series(candles: tuple[Candle, ...]) -> CandleSeries:
    return CandleSeries.from_closed(
        symbol=SYMBOL,
        timeframe="4h",
        candles=candles,
        now_ms=candles[-1].timestamp + TF,
    )


def test_atr_wilder_seed_and_update() -> None:
    candles = (
        _hlc(0, 10.0, 8.0, 9.0),
        _hlc(1, 12.0, 9.0, 11.0),
        _hlc(2, 13.0, 10.0, 12.0),
        _hlc(3, 14.0, 11.0, 13.0),
    )
    tr1 = true_range(candles[1], candles[0].close)
    tr2 = true_range(candles[2], candles[1].close)
    tr3 = true_range(candles[3], candles[2].close)
    result = atr(_ohlc_series(candles), 2)
    assert result.points[1].skip_reason == SKIP_WARMUP
    assert result.points[2].value == pytest.approx((tr1 + tr2) / 2.0)
    assert result.points[3].value == pytest.approx((((tr1 + tr2) / 2.0) + tr3) / 2.0)


def test_atr_zero_is_a_value() -> None:
    candles = (_hlc(0, 5.0, 5.0, 5.0), _hlc(1, 5.0, 5.0, 5.0), _hlc(2, 5.0, 5.0, 5.0))
    result = atr(_ohlc_series(candles), 1)
    assert result.points[1].value == pytest.approx(0.0)
    assert result.points[1].skip_reason is None


def test_atr_default_first_valid_index() -> None:
    candles = tuple(_hlc(i, 10.0 + i, 8.0 + i, 9.0 + i) for i in range(20))
    result = atr(_ohlc_series(candles))
    assert ATR_PERIOD == 14
    assert result.points[13].skip_reason == SKIP_WARMUP
    assert result.points[14].value is not None


def test_atr_gap_and_invalid_ohlc() -> None:
    pre = (_hlc(0, 10.0, 8.0, 9.0), _hlc(1, 12.0, 9.0, 11.0), _hlc(2, 13.0, 10.0, 12.0))
    post = (_hlc(0, 20.0, 18.0, 19.0, start=8), _hlc(1, 22.0, 19.0, 21.0, start=8))
    gapped = atr(_ohlc_series(pre + post), 2)
    assert gapped.points[2].value is not None
    assert gapped.points[3].skip_reason == SKIP_WARMUP
    nan_bar = Candle(timestamp=TF, open=1.0, high=math.nan, low=1.0, close=1.0, volume=1.0)
    assert atr(_ohlc_series((_hlc(0, 2.0, 1.0, 1.5), nan_bar)), 2).points[1].skip_reason == SKIP_NON_FINITE
    bad = _hlc(1, 1.0, 3.0, 2.0)
    assert atr(_ohlc_series((_hlc(0, 2.0, 1.0, 1.5), bad)), 2).points[1].skip_reason == SKIP_UNDEFINED


def test_atr_prefix_matches_full() -> None:
    candles = tuple(_hlc(i, 10.0 + i, 8.0 + 0.5 * i, 9.0 + 0.8 * i) for i in range(8))
    full = atr(_ohlc_series(candles), 3)
    for end in range(2, len(candles) + 1):
        prefix = atr(_ohlc_series(candles[:end]), 3)
        assert prefix.points[-1] == full.points[end - 1]


def test_population_stdev_not_sample() -> None:
    values = [2.0, 4.0, 6.0]
    mean = 4.0
    pop = math.sqrt(8.0 / 3.0)
    sample = math.sqrt(4.0)
    assert population_stdev(values, mean) == pytest.approx(pop)
    assert population_stdev(values, mean) != pytest.approx(sample)


def test_bollinger_known_values_and_width_ratio() -> None:
    result = bollinger_bands(_close_series([2.0, 4.0, 6.0]), period=3, std_multiplier=2.0)
    mean = 4.0
    stdev = math.sqrt(8.0 / 3.0)
    assert result.middle.points[2].value == pytest.approx(mean)
    assert result.middle.points[2].value == pytest.approx(sma(_close_series([2.0, 4.0, 6.0]), 3).points[2].value)
    assert result.upper.points[2].value == pytest.approx(mean + 2.0 * stdev)
    assert result.lower.points[2].value == pytest.approx(mean - 2.0 * stdev)
    assert result.width.points[2].value == pytest.approx(4.0 * stdev / mean)


def test_bollinger_zero_middle_width_undefined() -> None:
    result = bollinger_bands(_close_series([-1.0, 0.0, 1.0]), period=3, std_multiplier=2.0)
    assert result.middle.points[2].value == pytest.approx(0.0)
    assert result.upper.points[2].value is not None
    assert result.lower.points[2].value is not None
    assert result.width.points[2].skip_reason == SKIP_UNDEFINED


def test_bollinger_constant_prices_zero_width() -> None:
    result = bollinger_bands(_close_series([5.0, 5.0, 5.0, 5.0]), period=3, std_multiplier=2.0)
    assert result.middle.points[2].value == pytest.approx(5.0)
    assert result.width.points[2].value == pytest.approx(0.0)


def test_bollinger_default_warmup_and_prefix() -> None:
    series = _close_series([10.0 + i * 0.1 for i in range(25)])
    result = bollinger_bands(series)
    assert BB_PERIOD == 20
    assert result.middle.points[18].skip_reason == SKIP_WARMUP
    assert result.width.points[19].value is not None
    prefix = CandleSeries.from_closed(
        symbol=SYMBOL,
        timeframe="4h",
        candles=series.candles[:21],
        now_ms=series.candles[20].timestamp + TF,
    )
    assert bollinger_bands(prefix).width.points[-1] == result.width.points[20]

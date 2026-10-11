import math

import pytest

from tradingbot.data.ohlcv.models import Candle
from tradingbot.selection.rank_features import (
    RankFeaturesError,
    rank_features_4h,
)
from tradingbot.strategy.models import CandleSeries

TF = 14_400_000
SYMBOL = "BTC/USDT"


def _c(index: int, *, close: float = 1.0, volume: float = 1.0) -> Candle:
    return Candle(
        timestamp=index * TF,
        open=close,
        high=close,
        low=close,
        close=close,
        volume=volume,
    )


def _closed(candles: tuple[Candle, ...], *, extra_ms: int = TF) -> CandleSeries:
    last = candles[-1].timestamp
    return CandleSeries.from_closed(
        symbol=SYMBOL,
        timeframe="4h",
        candles=candles,
        now_ms=last + extra_ms,
    )


def test_50_closed_candles_match_hand_calculated_features() -> None:
    candles = tuple(_c(index, close=50.0 + index, volume=1.0) for index in range(50))
    series = _closed(candles)
    features = rank_features_4h(series)
    sma20 = 89.5
    sma50 = 74.5
    assert features.sma20 == sma20
    assert features.sma50 == sma50
    assert features.close_vs_sma50_pct == (99.0 / sma50 - 1) * 100
    assert features.ma_spread_pct == (sma20 / sma50 - 1) * 100
    assert features.roc6_pct == (99.0 / 93.0 - 1) * 100
    assert features.mean_base_volume20 == 1.0
    assert all(
        math.isfinite(value)
        for value in (
            features.sma20,
            features.sma50,
            features.close_vs_sma50_pct,
            features.ma_spread_pct,
            features.roc6_pct,
            features.mean_base_volume20,
        )
    )


def test_49_candles_raise() -> None:
    candles = tuple(_c(index) for index in range(49))
    series = _closed(candles)
    with pytest.raises(RankFeaturesError, match="at least 50"):
        rank_features_4h(series)


def test_gap_in_last_50_raises() -> None:
    candles = tuple(_c(index) for index in range(49)) + (_c(50),)
    series = _closed(candles)
    with pytest.raises(RankFeaturesError, match="gap in last 50"):
        rank_features_4h(series)


def test_unaligned_timestamp_in_last_50_raises() -> None:
    candles = [_c(index) for index in range(50)]
    candles[10] = Candle(
        timestamp=10 * TF + 60_000,
        open=1.0,
        high=1.0,
        low=1.0,
        close=1.0,
        volume=1.0,
    )
    series = _closed(tuple(candles))
    with pytest.raises(RankFeaturesError, match="unaligned 4h timestamp"):
        rank_features_4h(series)


def test_gap_before_last_50_is_ignored() -> None:
    candles = (_c(0, close=1.0),) + tuple(
        _c(index, close=50.0 + index) for index in range(2, 52)
    )
    features = rank_features_4h(_closed(candles))
    assert features.sma50 == 76.5
    assert features.sma20 == 91.5


def test_open_candle_is_rejected_by_from_closed() -> None:
    candles = tuple(_c(index) for index in range(50))
    with pytest.raises(ValueError, match="incomplete"):
        CandleSeries.from_closed(
            symbol=SYMBOL,
            timeframe="4h",
            candles=candles,
            now_ms=49 * TF + 1,
        )


def test_zero_close_raises() -> None:
    candles = tuple(_c(index, close=1.0) for index in range(49)) + (_c(49, close=0.0),)
    series = _closed(candles)
    with pytest.raises(RankFeaturesError, match="close must be positive"):
        rank_features_4h(series)


def test_negative_close_raises() -> None:
    candles = tuple(_c(index, close=1.0) for index in range(49)) + (_c(49, close=-1.0),)
    series = _closed(candles)
    with pytest.raises(RankFeaturesError, match="close must be positive"):
        rank_features_4h(series)


def test_negative_volume_raises() -> None:
    candles = tuple(_c(index, volume=1.0) for index in range(49)) + (_c(49, volume=-0.1),)
    series = _closed(candles)
    with pytest.raises(RankFeaturesError, match="negative volume"):
        rank_features_4h(series)


def test_zero_volume_is_allowed() -> None:
    candles = tuple(_c(index, close=2.0, volume=0.0) for index in range(50))
    features = rank_features_4h(_closed(candles))
    assert features.mean_base_volume20 == 0.0
    assert math.isfinite(features.mean_base_volume20)


def test_roc6_uses_close_seven_points_back() -> None:
    candles = [_c(index, close=100.0) for index in range(50)]
    candles[-8] = _c(42, close=50.0)
    candles[-7] = _c(43, close=100.0)
    candles[-6] = _c(44, close=200.0)
    candles[-1] = _c(49, close=110.0)
    features = rank_features_4h(_closed(tuple(candles)))
    assert features.roc6_pct == (110.0 / 100.0 - 1) * 100


def test_input_series_is_not_mutated() -> None:
    candles = tuple(_c(index, close=50.0 + index) for index in range(50))
    series = _closed(candles)
    original = series.candles
    rank_features_4h(series)
    assert series.candles is original
    assert series.candles == candles


def test_non_4h_timeframe_raises() -> None:
    candles = tuple(
        Candle(
            timestamp=index * 3_600_000,
            open=1.0,
            high=1.0,
            low=1.0,
            close=1.0,
            volume=1.0,
        )
        for index in range(50)
    )
    series = CandleSeries.from_closed(
        symbol=SYMBOL,
        timeframe="1h",
        candles=candles,
        now_ms=50 * 3_600_000,
    )
    with pytest.raises(RankFeaturesError, match="timeframe '4h'"):
        rank_features_4h(series)

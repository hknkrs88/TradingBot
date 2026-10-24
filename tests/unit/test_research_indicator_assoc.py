import math

from tradingbot.data.ohlcv.models import Candle
from tradingbot.indicators import sma
from tradingbot.research.indicator_assoc import correlate_features, feature_series
from tradingbot.selection.rank_features import rank_features_4h
from tradingbot.strategy.models import CandleSeries

TF = 14_400_000


def _series(n: int = 60) -> CandleSeries:
    candles = tuple(
        Candle(
            timestamp=i * TF,
            open=10.0 + i * 0.2 + math.sin(i / 3.0),
            high=10.4 + i * 0.2 + math.sin(i / 3.0),
            low=9.6 + i * 0.2 + math.sin(i / 3.0),
            close=10.1 + i * 0.2 + math.sin(i / 3.0),
            volume=float(i + 1),
        )
        for i in range(n)
    )
    return CandleSeries.from_closed(
        symbol="AAA/USDT",
        timeframe="4h",
        candles=candles,
        now_ms=candles[-1].timestamp + TF,
    )


def test_score_features_match_rank_features_at_last_bar() -> None:
    series = _series()
    feats = feature_series(series)
    ranked = rank_features_4h(series)
    last_close_vs = feats["close_vs_sma50_pct"][-1]
    last_spread = feats["ma_spread_pct"][-1]
    last_roc = feats["roc6_pct"][-1]
    assert last_close_vs.timestamp == series.candles[-1].timestamp
    assert last_close_vs.value == ranked.close_vs_sma50_pct
    assert last_spread.value == ranked.ma_spread_pct
    assert last_roc.value == ranked.roc6_pct


def test_features_at_t_match_prefix_no_lookahead() -> None:
    full = _series(70)
    prefix = _series(55)
    full_feats = feature_series(full)
    prefix_feats = feature_series(prefix)
    ts = prefix.candles[-1].timestamp
    for name in ("rsi14", "macd_histogram", "adx14", "atr_over_close", "close_vs_sma50_pct"):
        full_at = next(point.value for point in full_feats[name] if point.timestamp == ts)
        prefix_at = next(point.value for point in prefix_feats[name] if point.timestamp == ts)
        assert full_at == prefix_at


def test_atr_over_close_skips_non_positive_close() -> None:
    candles = list(_series(20).candles)
    bad = candles[15]
    candles[15] = Candle(
        timestamp=bad.timestamp,
        open=1.0,
        high=1.0,
        low=1.0,
        close=0.0,
        volume=1.0,
    )
    series = CandleSeries.from_closed(
        symbol="AAA/USDT",
        timeframe="4h",
        candles=tuple(candles),
        now_ms=candles[-1].timestamp + TF,
    )
    feats = feature_series(series)
    assert bad.timestamp not in {point.timestamp for point in feats["atr_over_close"]}


def test_correlate_uses_same_timestamp_and_reports_n() -> None:
    series = _series(80)
    table = correlate_features(series, (1, 3))
    assert table["rsi14"][1].n > 0
    assert table["close_vs_sma50_pct"][1].rho is not None
    sma50 = sma(series, 50)
    valued = sum(1 for point in sma50.points if point.value is not None)
    assert table["close_vs_sma50_pct"][1].n == valued - 1

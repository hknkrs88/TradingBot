"""Deterministic 4h MA, momentum, and base-volume features. No ranking or orders.

Inputs must be a CandleSeries of closed bars only. Callers build that series with
CandleSeries.from_closed(..., now_ms=...). This module does not take now_ms and
does not check whether bars are still open. Later selection should pass the same
now_ms used by the 4h sufficiency scan.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from tradingbot.data.ohlcv.timeframes import timeframe_ms
from tradingbot.strategy.models import CandleSeries

SMA_FAST = 20
SMA_SLOW = 50
ROC_BARS = 6
VOLUME_WINDOW = 20


class RankFeaturesError(ValueError):
    """A 4h CandleSeries cannot yield rank features."""


@dataclass(frozen=True)
class RankFeatures4h:
    sma20: float
    sma50: float
    close_vs_sma50_pct: float
    ma_spread_pct: float
    roc6_pct: float
    mean_base_volume20: float


def rank_features_4h(series: CandleSeries) -> RankFeatures4h:
    """Compute 4h features from a closed CandleSeries. Does not mutate series.

    ``series`` must contain only closed candles. The caller must construct it via
    ``CandleSeries.from_closed(..., now_ms=...)``. This function does not verify
    closedness. Selection integration must reuse the sufficiency scan's now_ms.
    """
    if series.timeframe != "4h":
        raise RankFeaturesError(f"rank features require timeframe '4h', got {series.timeframe!r}")
    candles = series.candles
    if len(candles) < SMA_SLOW:
        raise RankFeaturesError(f"need at least {SMA_SLOW} closed candles, got {len(candles)}")

    window = candles[-SMA_SLOW:]
    tf_ms = timeframe_ms("4h")
    _require_consecutive_4h(window, tf_ms)
    for candle in window:
        if candle.close <= 0:
            raise RankFeaturesError("close must be positive")
        if candle.volume < 0:
            raise RankFeaturesError("negative volume")

    closes = [item.close for item in window]
    volumes = [item.volume for item in window]
    last_close = closes[-1]
    close_7 = closes[-(ROC_BARS + 1)]
    sma20 = _mean(closes[-SMA_FAST:])
    sma50 = _mean(closes)
    mean_volume = _mean(volumes[-VOLUME_WINDOW:])
    if sma50 == 0 or close_7 == 0:
        raise RankFeaturesError("undefined ratio")
    features = RankFeatures4h(
        sma20=sma20,
        sma50=sma50,
        close_vs_sma50_pct=(last_close / sma50 - 1) * 100,
        ma_spread_pct=(sma20 / sma50 - 1) * 100,
        roc6_pct=(last_close / close_7 - 1) * 100,
        mean_base_volume20=mean_volume,
    )
    for name, value in (
        ("sma20", features.sma20),
        ("sma50", features.sma50),
        ("close_vs_sma50_pct", features.close_vs_sma50_pct),
        ("ma_spread_pct", features.ma_spread_pct),
        ("roc6_pct", features.roc6_pct),
        ("mean_base_volume20", features.mean_base_volume20),
    ):
        if not math.isfinite(value):
            raise RankFeaturesError(f"non-finite rank feature {name}")
    return features


def _mean(values: list[float]) -> float:
    return sum(values) / len(values)


def _require_consecutive_4h(window, tf_ms: int) -> None:
    stamps = [item.timestamp for item in window]
    if any(ts % tf_ms != 0 for ts in stamps):
        raise RankFeaturesError("unaligned 4h timestamp in feature window")
    for left, right in zip(stamps, stamps[1:]):
        if right - left != tf_ms:
            raise RankFeaturesError("gap in last 50 4h candles")

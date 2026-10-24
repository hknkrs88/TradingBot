"""Decision-time ranking using production score functions on closed prefixes.

Does not call ``select_top_4h_candidates`` (that path would score the on-disk
universe at one ``now_ms``). Uses the same ``rank_features_4h``,
``score_features_4h``, and ``(-score, original_index)`` order.

Only prefixes whose last closed bar is aligned with ``now_ms`` are scored:
``last_timestamp + timeframe_ms == now_ms``. One timeframe behind is excluded.
This is a freshness gate, not a score threshold.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from tradingbot.data.ohlcv.timeframes import timeframe_ms
from tradingbot.selection.rank_features import RankFeaturesError, rank_features_4h
from tradingbot.selection.rank_score import RankScoreError, score_features_4h
from tradingbot.strategy.models import CandleSeries


def is_current_closed_prefix(
    series: CandleSeries,
    *,
    now_ms: int,
    timeframe: str,
) -> bool:
    """True when the last closed bar is exactly the bar that closed at ``now_ms``."""
    if not series.candles:
        return False
    last = series.candles[-1].timestamp
    return last + timeframe_ms(timeframe) == now_ms


def rank_closed_prefixes(
    prefixes: Mapping[str, CandleSeries],
    symbol_order: Sequence[str],
    *,
    now_ms: int,
    timeframe: str = "4h",
) -> tuple[str, ...]:
    """Return current symbols best-first. Fail-closed skips stale or unscoreable series."""
    scored: list[tuple[int, str, float]] = []
    for index, symbol in enumerate(symbol_order):
        series = prefixes.get(symbol)
        if series is None or not series.candles:
            continue
        if not is_current_closed_prefix(series, now_ms=now_ms, timeframe=timeframe):
            continue
        try:
            features = rank_features_4h(series)
            score = score_features_4h(features)
        except (RankFeaturesError, RankScoreError, ValueError):
            continue
        scored.append((index, symbol, score))
    scored.sort(key=lambda row: (-row[2], row[0]))
    return tuple(item[1] for item in scored)

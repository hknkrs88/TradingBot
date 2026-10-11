"""Orchestrate 4h scan, features, scoring, and top-5. No orders or extra I/O."""

from __future__ import annotations

from pathlib import Path

from tradingbot.data.ohlcv.universe_sufficiency import assess_universe_4h
from tradingbot.selection.candidate_features import candidate_features_4h
from tradingbot.selection.rank_score import rank_scored_candidates
from tradingbot.selection.top_candidates import TopCandidatesReport, select_top_candidates


def select_top_4h_candidates(
    *,
    universe_path: Path,
    root: Path,
    now_ms: int,
) -> TopCandidatesReport:
    """Scan, feature, score, then take at most five. Does not catch errors."""
    scan = assess_universe_4h(universe_path=universe_path, root=root, now_ms=now_ms)
    features = candidate_features_4h(scan, root=root)
    ranked = rank_scored_candidates(features)
    return select_top_candidates(ranked)

"""Pure 4h feature scores and deterministic ranking. No I/O, journal, or orders."""

from __future__ import annotations

import math
from dataclasses import dataclass

from tradingbot.selection.candidate_features import (
    CandidateFeatureRejection,
    CandidateFeaturesReport,
)
from tradingbot.selection.rank_features import RankFeatures4h


class RankScoreError(ValueError):
    """A RankFeatures4h object cannot yield a finite score."""


@dataclass(frozen=True)
class ScoredCandidate:
    symbol: str
    score: float


@dataclass(frozen=True)
class RankScoreRejection:
    symbol: str
    reason: str


@dataclass(frozen=True)
class RankedCandidatesReport:
    """Ranking output. ``rejected`` keeps prior CandidateFeatureRejection rows
    unchanged (same objects) and appends scoring-stage RankScoreRejection rows.
    """

    scored: tuple[ScoredCandidate, ...]
    rejected: tuple[CandidateFeatureRejection | RankScoreRejection, ...]


def score_features_4h(features: RankFeatures4h) -> float:
    """Return close_vs_sma50_pct + ma_spread_pct + roc6_pct. Does not mutate features."""
    close_vs = features.close_vs_sma50_pct
    spread = features.ma_spread_pct
    roc = features.roc6_pct
    for name, value in (
        ("close_vs_sma50_pct", close_vs),
        ("ma_spread_pct", spread),
        ("roc6_pct", roc),
    ):
        if not math.isfinite(value):
            raise RankScoreError(f"non-finite score component {name}")
    score = close_vs + spread + roc
    if not math.isfinite(score):
        raise RankScoreError("non-finite score")
    return score


def rank_scored_candidates(report: CandidateFeaturesReport) -> RankedCandidatesReport:
    """Score report.features only. Does not mutate report or rerun the safety gate."""
    scored_rows: list[tuple[int, ScoredCandidate]] = []
    score_rejections: list[RankScoreRejection] = []
    for index, item in enumerate(report.features):
        try:
            score = score_features_4h(item.features)
        except RankScoreError:
            score_rejections.append(
                RankScoreRejection(symbol=item.symbol, reason="non_finite_score")
            )
            continue
        scored_rows.append((index, ScoredCandidate(symbol=item.symbol, score=score)))
    scored_rows.sort(key=lambda row: (-row[1].score, row[0]))
    return RankedCandidatesReport(
        scored=tuple(item for _, item in scored_rows),
        rejected=tuple(report.rejected) + tuple(score_rejections),
    )

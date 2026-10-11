"""Take at most five already-ranked 4h candidates. No rescore, I/O, or orders."""

from __future__ import annotations

from dataclasses import dataclass

from tradingbot.selection.candidate_features import CandidateFeatureRejection
from tradingbot.selection.rank_score import RankedCandidatesReport, RankScoreRejection, ScoredCandidate

TOP_CANDIDATES = 5


@dataclass(frozen=True)
class TopCandidatesReport:
    """First ``TOP_CANDIDATES`` scored rows in input order. ``rejected`` keeps
    prior rows unchanged (same objects, same order).
    """

    selected: tuple[ScoredCandidate, ...]
    rejected: tuple[CandidateFeatureRejection | RankScoreRejection, ...]


def select_top_candidates(report: RankedCandidatesReport) -> TopCandidatesReport:
    """Slice report.scored to at most five. Does not mutate report or re-rank."""
    return TopCandidatesReport(
        selected=report.scored[:TOP_CANDIDATES],
        rejected=tuple(report.rejected),
    )

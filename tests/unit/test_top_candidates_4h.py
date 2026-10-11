import pytest

from tradingbot.selection.candidate_features import CandidateFeatureRejection
from tradingbot.selection.rank_score import RankedCandidatesReport, RankScoreRejection, ScoredCandidate
from tradingbot.selection.top_candidates import (
    TOP_CANDIDATES,
    TopCandidatesReport,
    select_top_candidates,
)

BTC = "BTC/USDT"
ETH = "ETH/USDT"
SOL = "SOL/USDT"
XRP = "XRP/USDT"
ADA = "ADA/USDT"
DOT = "DOT/USDT"


def _scored(symbol: str, score: float) -> ScoredCandidate:
    return ScoredCandidate(symbol=symbol, score=score)


def _report(
    scored: tuple[ScoredCandidate, ...],
    rejected: tuple[CandidateFeatureRejection | RankScoreRejection, ...] = (),
) -> RankedCandidatesReport:
    return RankedCandidatesReport(scored=scored, rejected=rejected)


def test_six_candidates_select_exactly_first_five() -> None:
    first_five = (
        _scored(BTC, 9),
        _scored(ETH, 8),
        _scored(SOL, 7),
        _scored(XRP, 6),
        _scored(ADA, 5),
    )
    ranked = _report(first_five + (_scored(DOT, 4),))
    top = select_top_candidates(ranked)
    assert TOP_CANDIDATES == 5
    assert isinstance(top, TopCandidatesReport)
    assert top.selected == first_five
    assert [item.symbol for item in top.selected] == [BTC, ETH, SOL, XRP, ADA]
    assert len(top.selected) == 5
    assert DOT not in {item.symbol for item in top.selected}


def test_fewer_than_five_returns_all_in_input_order() -> None:
    scored = (
        _scored(ETH, 1),
        _scored(BTC, 3),
        _scored(SOL, 2),
    )
    top = select_top_candidates(_report(scored))
    assert top.selected == scored
    assert [item.symbol for item in top.selected] == [ETH, BTC, SOL]
    assert len(top.selected) == 3


def test_empty_scored_returns_empty_selected() -> None:
    prior = (CandidateFeatureRejection(symbol=BTC, reason="missing_processed_cache"),)
    ranked = _report((), rejected=prior)
    top = select_top_candidates(ranked)
    assert top.selected == ()
    assert top.rejected == prior
    assert top.rejected[0] is prior[0]


def test_selected_keeps_ranked_input_order() -> None:
    scored = (
        _scored(SOL, 4),
        _scored(ETH, 4),
        _scored(BTC, 1),
        _scored(XRP, 0),
        _scored(ADA, -1),
        _scored(DOT, -2),
    )
    top = select_top_candidates(_report(scored))
    assert [item.symbol for item in top.selected] == [SOL, ETH, BTC, XRP, ADA]
    assert [item.score for item in top.selected] == [4, 4, 1, 0, -1]


def test_rejected_rows_are_preserved_in_order_and_identity() -> None:
    prior_feature = CandidateFeatureRejection(symbol=ETH, reason="symbol_mismatch")
    prior_score = RankScoreRejection(symbol=DOT, reason="non_finite_score")
    rejected = (prior_feature, prior_score)
    ranked = _report((_scored(BTC, 1),), rejected=rejected)
    top = select_top_candidates(ranked)
    assert top.rejected == rejected
    assert top.rejected[0] is prior_feature
    assert top.rejected[1] is prior_score
    assert top.rejected[0].symbol == "ETH/USDT"
    assert top.rejected[0].reason == "symbol_mismatch"
    assert top.rejected[1].symbol == "DOT/USDT"
    assert top.rejected[1].reason == "non_finite_score"


def test_input_report_is_not_mutated() -> None:
    scored = (_scored(BTC, 1), _scored(ETH, 0))
    prior = (CandidateFeatureRejection(symbol=SOL, reason="too_few_closed_candles"),)
    ranked = RankedCandidatesReport(scored=scored, rejected=prior)
    top = select_top_candidates(ranked)
    assert ranked.scored is scored
    assert ranked.rejected is prior
    assert ranked.scored == (_scored(BTC, 1), _scored(ETH, 0))
    assert top.selected == scored
    assert top.selected[0] is scored[0]


def test_unexpected_runtime_error_is_not_swallowed() -> None:
    class BrokenReport:
        @property
        def scored(self) -> tuple[ScoredCandidate, ...]:
            raise RuntimeError("programming bug")

        rejected: tuple[()] = ()

    with pytest.raises(RuntimeError, match="programming bug"):
        select_top_candidates(BrokenReport())  # type: ignore[arg-type]


def test_selection_is_repeatable() -> None:
    ranked = _report(
        (
            _scored(ETH, 2),
            _scored(BTC, 2),
            _scored(SOL, 9),
        )
    )
    first = select_top_candidates(ranked)
    second = select_top_candidates(ranked)
    assert first == second
    assert [item.symbol for item in first.selected] == [ETH, BTC, SOL]

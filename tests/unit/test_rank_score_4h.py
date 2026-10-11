import math

import pytest

from tradingbot.selection.candidate_features import (
    CandidateFeatureRejection,
    CandidateFeatures,
    CandidateFeaturesReport,
)
from tradingbot.selection.rank_features import RankFeatures4h
from tradingbot.selection.rank_score import (
    RankScoreError,
    RankScoreRejection,
    rank_scored_candidates,
    score_features_4h,
)

BTC = "BTC/USDT"
ETH = "ETH/USDT"
SOL = "SOL/USDT"


def _features(
    *,
    sma20: float = 1.0,
    sma50: float = 1.0,
    close_vs_sma50_pct: float = 0.0,
    ma_spread_pct: float = 0.0,
    roc6_pct: float = 0.0,
    mean_base_volume20: float = 0.0,
) -> RankFeatures4h:
    return RankFeatures4h(
        sma20=sma20,
        sma50=sma50,
        close_vs_sma50_pct=close_vs_sma50_pct,
        ma_spread_pct=ma_spread_pct,
        roc6_pct=roc6_pct,
        mean_base_volume20=mean_base_volume20,
    )


def _report(
    items: list[CandidateFeatures],
    rejected: tuple[CandidateFeatureRejection, ...] = (),
) -> CandidateFeaturesReport:
    return CandidateFeaturesReport(
        now_ms=1,
        features=tuple(items),
        rejected=rejected,
    )


def _cand(symbol: str, features: RankFeatures4h) -> CandidateFeatures:
    return CandidateFeatures(symbol=symbol, features=features)


def test_hand_calculated_score_is_17() -> None:
    features = _features(close_vs_sma50_pct=10, ma_spread_pct=5, roc6_pct=2)
    assert score_features_4h(features) == 17


def test_zero_percentages_score_zero() -> None:
    assert score_features_4h(_features()) == 0


def test_negative_momentum_lowers_score() -> None:
    features = _features(close_vs_sma50_pct=10, ma_spread_pct=5, roc6_pct=-3)
    assert score_features_4h(features) == 12


def test_sma_and_volume_do_not_affect_score() -> None:
    base = _features(
        sma20=10.0,
        sma50=20.0,
        close_vs_sma50_pct=1.0,
        ma_spread_pct=2.0,
        roc6_pct=3.0,
        mean_base_volume20=100.0,
    )
    other = _features(
        sma20=999.0,
        sma50=0.5,
        close_vs_sma50_pct=1.0,
        ma_spread_pct=2.0,
        roc6_pct=3.0,
        mean_base_volume20=0.0,
    )
    assert score_features_4h(base) == score_features_4h(other) == 6


@pytest.mark.parametrize(
    "field",
    ("close_vs_sma50_pct", "ma_spread_pct", "roc6_pct"),
)
@pytest.mark.parametrize("value", (math.nan, math.inf, -math.inf))
def test_non_finite_component_raises_rank_score_error(field: str, value: float) -> None:
    features = _features(**{field: value})
    with pytest.raises(RankScoreError, match="non-finite score component"):
        score_features_4h(features)


def test_non_finite_sum_raises_rank_score_error() -> None:
    features = _features(
        close_vs_sma50_pct=1e308,
        ma_spread_pct=1e308,
        roc6_pct=0.0,
    )
    with pytest.raises(RankScoreError, match="non-finite score"):
        score_features_4h(features)


def test_candidates_are_sorted_by_score_descending() -> None:
    report = _report(
        [
            _cand(ETH, _features(close_vs_sma50_pct=1)),
            _cand(BTC, _features(close_vs_sma50_pct=5)),
            _cand(SOL, _features(close_vs_sma50_pct=3)),
        ]
    )
    ranked = rank_scored_candidates(report)
    assert [item.symbol for item in ranked.scored] == [BTC, SOL, ETH]
    assert [item.score for item in ranked.scored] == [5, 3, 1]


def test_equal_scores_keep_original_feature_order() -> None:
    report = _report(
        [
            _cand(ETH, _features(close_vs_sma50_pct=2)),
            _cand(BTC, _features(ma_spread_pct=2)),
            _cand(SOL, _features(roc6_pct=4)),
        ]
    )
    ranked = rank_scored_candidates(report)
    assert [item.symbol for item in ranked.scored] == [SOL, ETH, BTC]
    assert ranked.scored[1].score == ranked.scored[2].score == 2


def test_non_finite_candidate_is_rejected_others_continue() -> None:
    report = _report(
        [
            _cand(ETH, _features(close_vs_sma50_pct=4)),
            _cand(BTC, _features(roc6_pct=math.nan)),
            _cand(SOL, _features(close_vs_sma50_pct=1)),
        ]
    )
    ranked = rank_scored_candidates(report)
    assert [item.symbol for item in ranked.scored] == [ETH, SOL]
    assert ranked.rejected == (RankScoreRejection(symbol=BTC, reason="non_finite_score"),)


def test_prior_rejections_are_preserved() -> None:
    prior = CandidateFeatureRejection(symbol=ETH, reason="symbol_mismatch")
    report = _report(
        [_cand(BTC, _features(close_vs_sma50_pct=1))],
        rejected=(prior,),
    )
    ranked = rank_scored_candidates(report)
    assert ranked.rejected[0] is prior
    assert ranked.rejected[0].symbol == "ETH/USDT"
    assert ranked.rejected[0].reason == "symbol_mismatch"
    assert ranked.scored[0].symbol == BTC


def test_unexpected_runtime_error_is_not_swallowed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    report = _report([_cand(BTC, _features())])

    def boom(features: RankFeatures4h) -> float:
        raise RuntimeError("programming bug")

    monkeypatch.setattr("tradingbot.selection.rank_score.score_features_4h", boom)
    with pytest.raises(RuntimeError, match="programming bug"):
        rank_scored_candidates(report)


def test_input_report_is_not_mutated() -> None:
    features = (_cand(BTC, _features(close_vs_sma50_pct=1)),)
    prior = (CandidateFeatureRejection(symbol=ETH, reason="too_few_closed_candles"),)
    report = CandidateFeaturesReport(now_ms=9, features=features, rejected=prior)
    ranked = rank_scored_candidates(report)
    assert report.features is features
    assert report.rejected is prior
    assert report.now_ms == 9
    assert ranked.scored[0].symbol == BTC


def test_ranking_is_repeatable() -> None:
    report = _report(
        [
            _cand(ETH, _features(close_vs_sma50_pct=2)),
            _cand(BTC, _features(close_vs_sma50_pct=2)),
            _cand(SOL, _features(close_vs_sma50_pct=9)),
        ]
    )
    first = rank_scored_candidates(report)
    second = rank_scored_candidates(report)
    assert first == second
    assert [item.symbol for item in first.scored] == [SOL, ETH, BTC]


def test_empty_features_returns_empty_scored() -> None:
    prior = (CandidateFeatureRejection(symbol=BTC, reason="missing_processed_cache"),)
    report = _report([], rejected=prior)
    ranked = rank_scored_candidates(report)
    assert ranked.scored == ()
    assert ranked.rejected == prior
    assert ranked.rejected[0] is prior[0]

import pytest

from tradingbot.research.spearman import spearman_corr


def test_spearman_perfect_monotone() -> None:
    result = spearman_corr([1.0, 2.0, 3.0, 4.0], [10.0, 20.0, 30.0, 40.0])
    assert result.n == 4
    assert result.reason is None
    assert result.rho == pytest.approx(1.0)


def test_spearman_inverse_monotone() -> None:
    result = spearman_corr([1.0, 2.0, 3.0, 4.0], [40.0, 30.0, 20.0, 10.0])
    assert result.rho == pytest.approx(-1.0)


def test_spearman_undefined_constant_and_short() -> None:
    assert spearman_corr([1.0], [2.0]).reason == "need_at_least_two_pairs"
    assert spearman_corr([1.0, 1.0, 1.0], [2.0, 3.0, 4.0]).reason == "constant_feature"
    assert spearman_corr([1.0, 2.0, 3.0], [5.0, 5.0, 5.0]).reason == "constant_forward_return"

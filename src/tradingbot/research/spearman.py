"""Spearman rank correlation without a second stats library."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass


@dataclass(frozen=True)
class SpearmanResult:
    n: int
    rho: float | None
    reason: str | None


def spearman_corr(xs: Sequence[float], ys: Sequence[float]) -> SpearmanResult:
    if len(xs) != len(ys):
        raise ValueError("xs and ys must have the same length")
    n = len(xs)
    if n < 2:
        return SpearmanResult(n=n, rho=None, reason="need_at_least_two_pairs")
    rx = _average_ranks(xs)
    ry = _average_ranks(ys)
    if _is_constant(rx):
        return SpearmanResult(n=n, rho=None, reason="constant_feature")
    if _is_constant(ry):
        return SpearmanResult(n=n, rho=None, reason="constant_forward_return")
    return SpearmanResult(n=n, rho=_pearson(rx, ry), reason=None)


def _average_ranks(values: Sequence[float]) -> list[float]:
    order = sorted(range(len(values)), key=lambda index: values[index])
    ranks = [0.0] * len(values)
    index = 0
    while index < len(order):
        end = index
        while end + 1 < len(order) and values[order[end + 1]] == values[order[index]]:
            end += 1
        avg = (index + end) / 2.0 + 1.0
        for pos in range(index, end + 1):
            ranks[order[pos]] = avg
        index = end + 1
    return ranks


def _is_constant(values: Sequence[float]) -> bool:
    return all(item == values[0] for item in values)


def _pearson(xs: Sequence[float], ys: Sequence[float]) -> float:
    n = float(len(xs))
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    num = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    den_x = sum((x - mean_x) ** 2 for x in xs) ** 0.5
    den_y = sum((y - mean_y) ** 2 for y in ys) ** 0.5
    return num / (den_x * den_y)

"""Equal cash split among new buys only. No rebalance."""

from __future__ import annotations


def equal_allocation(available_cash: float, n_new: int) -> float:
    """Return cash per new position. Does not rebalance open holdings."""
    if n_new < 0:
        raise ValueError("n_new must be >= 0")
    if n_new == 0:
        return 0.0
    if available_cash <= 0:
        return 0.0
    return available_cash / float(n_new)


def quantity_for_buy(*, allocation: float, buy_fill: float, commission_rate: float) -> float:
    """Largest quantity such that allocation covers notional plus entry commission."""
    if allocation <= 0 or buy_fill <= 0:
        return 0.0
    return allocation / (buy_fill * (1.0 + commission_rate))

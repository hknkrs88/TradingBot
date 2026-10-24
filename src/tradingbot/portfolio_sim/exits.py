"""Fill prices, fees, net take-profit, and net stop-loss. No orders."""

from __future__ import annotations


def buy_fill_price(reference_buy: float, slippage: float) -> float:
    """Adverse buy: pay more. ``slippage`` is a scenario fraction, not a quote."""
    if reference_buy <= 0:
        raise ValueError("reference_buy must be > 0")
    return reference_buy * (1.0 + slippage)


def sell_fill_price(reference_sell: float, slippage: float) -> float:
    """Adverse sell: receive less."""
    if reference_sell <= 0:
        raise ValueError("reference_sell must be > 0")
    return reference_sell * (1.0 - slippage)


def entry_fee_and_cost(*, quantity: float, buy_fill: float, commission_rate: float) -> tuple[float, float]:
    fee = quantity * buy_fill * commission_rate
    cost = quantity * buy_fill + fee
    return fee, cost


def exit_fee_and_proceeds(
    *, quantity: float, sell_fill: float, commission_rate: float
) -> tuple[float, float]:
    fee = quantity * sell_fill * commission_rate
    proceeds = quantity * sell_fill - fee
    return fee, proceeds


def take_profit_reached(
    *,
    entry_cost: float,
    quantity: float,
    sell_fill: float,
    commission_rate: float,
    net_profit_multiple: float = 1.01,
) -> bool:
    """True when net proceeds are at least ``net_profit_multiple`` times entry cost."""
    if entry_cost <= 0 or quantity <= 0:
        return False
    _fee, proceeds = exit_fee_and_proceeds(
        quantity=quantity, sell_fill=sell_fill, commission_rate=commission_rate
    )
    return proceeds + 1e-9 >= net_profit_multiple * entry_cost


def min_sell_fill_for_target(
    *,
    buy_fill: float,
    commission_rate: float,
    net_profit_multiple: float = 1.01,
) -> float:
    """Minimum sell fill (after applying sell-side slippage separately) for net target."""
    if buy_fill <= 0:
        raise ValueError("buy_fill must be > 0")
    if commission_rate >= 1:
        raise ValueError("commission_rate must be in [0, 1)")
    return net_profit_multiple * buy_fill * (1.0 + commission_rate) / (1.0 - commission_rate)


def stop_loss_reached(
    *,
    entry_cost: float,
    quantity: float,
    sell_fill: float,
    commission_rate: float,
    stop_loss_multiple: float = 0.97,
) -> bool:
    """True when net proceeds are at most ``stop_loss_multiple`` times entry cost."""
    if entry_cost <= 0 or quantity <= 0:
        return False
    _fee, proceeds = exit_fee_and_proceeds(
        quantity=quantity, sell_fill=sell_fill, commission_rate=commission_rate
    )
    return proceeds <= stop_loss_multiple * entry_cost + 1e-9


def max_sell_fill_for_stop(
    *,
    buy_fill: float,
    commission_rate: float,
    stop_loss_multiple: float = 0.97,
) -> float:
    """Maximum sell fill (sell-side slippage applied separately) that still trips net stop."""
    if buy_fill <= 0:
        raise ValueError("buy_fill must be > 0")
    if commission_rate >= 1:
        raise ValueError("commission_rate must be in [0, 1)")
    return stop_loss_multiple * buy_fill * (1.0 + commission_rate) / (1.0 - commission_rate)

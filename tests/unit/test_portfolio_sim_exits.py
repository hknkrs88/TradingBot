import pytest

from tradingbot.portfolio_sim.allocation import equal_allocation, quantity_for_buy
from tradingbot.portfolio_sim.exits import (
    buy_fill_price,
    entry_fee_and_cost,
    exit_fee_and_proceeds,
    max_sell_fill_for_stop,
    min_sell_fill_for_target,
    sell_fill_price,
    stop_loss_reached,
    take_profit_reached,
)
from tradingbot.portfolio_sim.models import PortfolioSimConfig, slippage_from_bps


def test_slippage_bps_conversion() -> None:
    assert slippage_from_bps(0) == 0.0
    assert slippage_from_bps(2) == pytest.approx(0.0002)
    assert slippage_from_bps(5) == pytest.approx(0.0005)
    assert slippage_from_bps(10) == pytest.approx(0.001)


def test_buy_fill_is_adverse() -> None:
    assert buy_fill_price(100.0, 0.001) == pytest.approx(100.1)
    assert sell_fill_price(100.0, 0.001) == pytest.approx(99.9)


def test_net_target_includes_commission_and_slippage() -> None:
    r = 0.001
    s = 0.0002
    ref_buy = 100.0
    buy_fill = buy_fill_price(ref_buy, s)
    qty = quantity_for_buy(allocation=1000.0, buy_fill=buy_fill, commission_rate=r)
    fee, cost = entry_fee_and_cost(quantity=qty, buy_fill=buy_fill, commission_rate=r)
    assert cost == pytest.approx(1000.0)
    min_sell = min_sell_fill_for_target(buy_fill=buy_fill, commission_rate=r)
    assert take_profit_reached(
        entry_cost=cost, quantity=qty, sell_fill=min_sell, commission_rate=r
    )
    assert not take_profit_reached(
        entry_cost=cost,
        quantity=qty,
        sell_fill=buy_fill * 1.01,
        commission_rate=r,
    )
    _exit_fee, proceeds = exit_fee_and_proceeds(
        quantity=qty, sell_fill=min_sell, commission_rate=r
    )
    assert proceeds == pytest.approx(1.01 * cost)


def test_net_stop_includes_commission_and_slippage() -> None:
    r = 0.001
    s = 0.0002
    ref_buy = 100.0
    buy_fill = buy_fill_price(ref_buy, s)
    qty = quantity_for_buy(allocation=1000.0, buy_fill=buy_fill, commission_rate=r)
    _fee, cost = entry_fee_and_cost(quantity=qty, buy_fill=buy_fill, commission_rate=r)
    max_sell = max_sell_fill_for_stop(buy_fill=buy_fill, commission_rate=r)
    assert stop_loss_reached(
        entry_cost=cost, quantity=qty, sell_fill=max_sell, commission_rate=r
    )
    assert not stop_loss_reached(
        entry_cost=cost,
        quantity=qty,
        sell_fill=max_sell * 1.001,
        commission_rate=r,
    )
    _exit_fee, proceeds = exit_fee_and_proceeds(
        quantity=qty, sell_fill=max_sell, commission_rate=r
    )
    assert proceeds == pytest.approx(0.97 * cost)
    assert max_sell != pytest.approx(buy_fill * 0.97)


def test_equal_allocation_does_not_exceed_cash() -> None:
    assert equal_allocation(100.0, 4) == pytest.approx(25.0)
    assert equal_allocation(100.0, 0) == 0.0
    assert equal_allocation(0.0, 3) == 0.0


def test_config_rejects_out_of_range_rates() -> None:
    with pytest.raises(ValueError, match="initial_cash"):
        PortfolioSimConfig(initial_cash=0, commission_rate=0.0)
    with pytest.raises(ValueError, match="commission_rate"):
        PortfolioSimConfig(initial_cash=100, commission_rate=-0.01)
    with pytest.raises(ValueError, match="commission_rate"):
        PortfolioSimConfig(initial_cash=100, commission_rate=1.0)
    with pytest.raises(ValueError, match="slippage"):
        PortfolioSimConfig(initial_cash=100, commission_rate=0.0, slippage=-0.1)
    with pytest.raises(ValueError, match="slippage"):
        PortfolioSimConfig(initial_cash=100, commission_rate=0.0, slippage=1.0)
    with pytest.raises(ValueError, match="stop_loss_multiple"):
        PortfolioSimConfig(initial_cash=100, commission_rate=0.0, stop_loss_multiple=1.0)
    with pytest.raises(ValueError, match="stop_loss_multiple"):
        PortfolioSimConfig(initial_cash=100, commission_rate=0.0, stop_loss_multiple=0.0)
    with pytest.raises(ValueError, match="net_profit_multiple"):
        PortfolioSimConfig(initial_cash=100, commission_rate=0.0, net_profit_multiple=1.0)
    allowed = PortfolioSimConfig(
        initial_cash=100, commission_rate=0.0, net_profit_multiple=None, stop_loss_multiple=0.97
    )
    assert allowed.net_profit_multiple is None
    assert allowed.stop_loss_multiple == 0.97


def test_default_config_keeps_tp_on_stop_off() -> None:
    config = PortfolioSimConfig(initial_cash=10_000.0, commission_rate=0.001)
    assert config.net_profit_multiple == 1.01
    assert config.stop_loss_multiple is None

"""Portfolio simulation metrics. Local only; no orders.

``total_slippage_cost`` is the sum of completed-trade scenario slippage only.
``open_entry_slippage_cost`` is buy-side scenario slippage still sitting in
open positions. The two are not added together into a third total here, so the
same fill is never counted twice.

``max_drawdown`` / ``max_drawdown_pct`` always come from the simulated
``result.equity`` path. ``mark_prices`` revalues only the *final* book
(``final_equity``, ``total_return``, open marks). That override is not applied
backward onto the equity curve.

Intra-bar stale marks are not on ``PortfolioEquityPoint``. The report can only
echo ``result.stale_mark_symbols`` (last timestamp). Scope is
``STALE_MARK_SCOPE_LAST_BAR_ONLY``; no age threshold is invented.
"""

from __future__ import annotations

from dataclasses import dataclass
from statistics import median

from tradingbot.portfolio_sim.exits import exit_fee_and_proceeds, sell_fill_price
from tradingbot.portfolio_sim.models import CompletedTrade, OpenPositionMark, PortfolioSimResult

STALE_MARK_SCOPE_LAST_BAR_ONLY = "last_bar_only"
MAX_DRAWDOWN_SOURCE_SIMULATED_CURVE = "simulated_equity_curve"


@dataclass(frozen=True)
class PortfolioPerformanceReport:
    """Definitions match unit tests. Survivorship-prone universe BH is labeled.

    Open marks use last closed closes (``result.last_marks`` or ``mark_prices``).
    ``final_equity`` and ``total_return`` use the same price map as
    ``open_mark_value`` and ``unrealized_pnl``: cash plus marked opens.
    ``max_drawdown`` is from the simulated equity curve only, not from
    ``mark_prices``. Unrealized P&L is mark value minus entry cost (no exit fee).
    ``hypothetical_liquidation_net`` applies scenario sell slippage and exit
    commission and is not realized P&L.
    Missing marks are not fabricated; ``analyze_portfolio`` raises instead.
    Trade stats use completed trades only (``pnl > 0`` win, ``pnl < 0`` loss).
    """

    initial_equity: float
    final_equity: float
    total_return: float
    max_drawdown: float
    max_drawdown_pct: float
    trade_count: int
    mean_trade_pnl: float | None
    median_trade_pnl: float | None
    win_rate: float | None
    winning_trade_count: int
    losing_trade_count: int
    scratch_trade_count: int
    profit_factor: float | None
    average_winning_trade_pnl: float | None
    average_losing_trade_pnl: float | None
    mean_hold_ms: float | None
    median_hold_ms: float | None
    mean_cash_ratio: float
    worst_trade_pnl: float | None
    tail_loss_p10: float | None
    max_concurrent_open: int
    total_commission: float
    total_slippage_cost: float
    open_entry_slippage_cost: float
    open_mark_value: float
    current_open_mark_value: float
    stale_open_mark_value: float
    unrealized_pnl: float
    hypothetical_liquidation_net: float
    open_marks: tuple[OpenPositionMark, ...]
    slippage_label: str
    survivorship_warning: str
    stale_mark_warning: str
    stale_mark_symbols: tuple[str, ...]
    stale_mark_scope: str
    max_drawdown_source: str


def _max_drawdown(equities: tuple[float, ...]) -> tuple[float, float]:
    peak = equities[0]
    max_dd = 0.0
    max_dd_pct = 0.0
    for equity in equities:
        if equity > peak:
            peak = equity
        drawdown = peak - equity
        drawdown_pct = drawdown / peak if peak > 0 else 0.0
        if drawdown_pct > max_dd_pct:
            max_dd_pct = drawdown_pct
            max_dd = drawdown
    return max_dd, max_dd_pct


def _completed_trade_stats(
    trades: tuple[CompletedTrade, ...],
) -> tuple[int, int, int, float | None, float | None, float | None]:
    """Wins ``pnl > 0``, losses ``pnl < 0``, scratches neither. PF undefined with no losses."""
    wins = [item.pnl for item in trades if item.pnl > 0]
    losses = [item.pnl for item in trades if item.pnl < 0]
    scratches = sum(1 for item in trades if item.pnl == 0)
    avg_win = (sum(wins) / len(wins)) if wins else None
    avg_loss = (sum(losses) / len(losses)) if losses else None
    if not trades or not losses:
        profit_factor = None
    else:
        profit_factor = sum(wins) / abs(sum(losses))
    return len(wins), len(losses), scratches, profit_factor, avg_win, avg_loss


def analyze_portfolio(
    result: PortfolioSimResult,
    *,
    mark_prices: dict[str, float] | None = None,
) -> PortfolioPerformanceReport:
    """Compute metrics from a finished ``PortfolioSimResult``.

    Open marks use ``mark_prices`` when provided. Otherwise they use
    ``result.last_marks`` (last known closed closes from the engine). Every open
    position must have a price in that map; missing symbols raise
    ``ValueError`` so a price is never invented and equity is never mixed with
    a second mark set.     Symbols in ``result.stale_mark_symbols`` are last-known closes at the
    **final** timestamp only (``stale_mark_scope`` =
    ``STALE_MARK_SCOPE_LAST_BAR_ONLY``). Per-bar stale sets are not on the
    equity points; they are not reconstructed or fabricated here.

    ``max_drawdown`` / ``max_drawdown_pct`` use ``result.equity``
    (``max_drawdown_source`` = ``MAX_DRAWDOWN_SOURCE_SIMULATED_CURVE``).
    ``mark_prices`` does not rewrite that path.
    """
    if not result.equity:
        raise ValueError("empty equity curve")
    initial = result.config.initial_cash
    if initial <= 0:
        raise ValueError("initial_cash must be > 0")
    max_dd, max_dd_pct = _max_drawdown(tuple(point.equity for point in result.equity))
    pnls = [item.pnl for item in result.trades]
    holds = [item.exit_timestamp - item.entry_timestamp for item in result.trades]
    trade_count = len(result.trades)
    wins = sum(1 for pnl in pnls if pnl > 0)
    win_n, lose_n, scratch_n, profit_factor, avg_win, avg_loss = _completed_trade_stats(
        result.trades
    )
    prices = mark_prices if mark_prices is not None else result.last_marks
    missing = sorted(
        {position.symbol for position in result.open_positions if position.symbol not in prices}
    )
    if missing:
        raise ValueError(
            "mark price missing for open position(s): "
            + ", ".join(missing)
            + "; prices are not fabricated"
        )
    stale_set = set(result.stale_mark_symbols)
    open_value = 0.0
    current_value = 0.0
    stale_value = 0.0
    unrealized = 0.0
    hypo_total = 0.0
    open_marks: list[OpenPositionMark] = []
    open_entry_slip = 0.0
    for position in result.open_positions:
        open_entry_slip += position.quantity * (position.buy_fill - position.reference_buy)
        mark = prices[position.symbol]
        value = position.quantity * mark
        open_value += value
        stale = position.symbol in stale_set
        if stale:
            stale_value += value
        else:
            current_value += value
        unrealized += value - position.entry_cost
        hypo_fill = sell_fill_price(mark, result.config.slippage)
        _fee, hypo_net = exit_fee_and_proceeds(
            quantity=position.quantity,
            sell_fill=hypo_fill,
            commission_rate=result.config.commission_rate,
        )
        hypo_total += hypo_net
        open_marks.append(
            OpenPositionMark(
                symbol=position.symbol,
                quantity=position.quantity,
                entry_cost=position.entry_cost,
                mark_price=mark,
                mark_value=value,
                unrealized_pnl=value - position.entry_cost,
                hypothetical_exit_net=hypo_net,
                stale=stale,
            )
        )
    total_commission = sum(item.entry_fee + item.exit_fee for item in result.trades)
    total_commission += sum(item.entry_fee for item in result.open_positions)
    marked_equity = result.final_cash + open_value
    return PortfolioPerformanceReport(
        initial_equity=initial,
        final_equity=marked_equity,
        total_return=(marked_equity - initial) / initial,
        max_drawdown=max_dd,
        max_drawdown_pct=max_dd_pct,
        trade_count=trade_count,
        mean_trade_pnl=(sum(pnls) / trade_count) if trade_count else None,
        median_trade_pnl=float(median(pnls)) if trade_count else None,
        win_rate=(wins / trade_count) if trade_count else None,
        winning_trade_count=win_n,
        losing_trade_count=lose_n,
        scratch_trade_count=scratch_n,
        profit_factor=profit_factor,
        average_winning_trade_pnl=avg_win,
        average_losing_trade_pnl=avg_loss,
        mean_hold_ms=(sum(holds) / trade_count) if trade_count else None,
        median_hold_ms=float(median(holds)) if trade_count else None,
        mean_cash_ratio=sum(point.cash_ratio for point in result.equity) / len(result.equity),
        worst_trade_pnl=min(pnls) if pnls else None,
        tail_loss_p10=_percentile(pnls, 10) if pnls else None,
        max_concurrent_open=int(result.metadata.get("max_open_positions") or 0),
        total_commission=total_commission,
        total_slippage_cost=sum(item.slippage_cost for item in result.trades),
        open_entry_slippage_cost=open_entry_slip,
        open_mark_value=open_value,
        current_open_mark_value=current_value,
        stale_open_mark_value=stale_value,
        unrealized_pnl=unrealized,
        hypothetical_liquidation_net=hypo_total,
        open_marks=tuple(open_marks),
        slippage_label=str(result.metadata.get("slippage_label") or result.config.slippage_label()),
        survivorship_warning=str(result.metadata.get("survivorship_warning") or ""),
        stale_mark_warning=str(result.metadata.get("stale_mark_warning") or ""),
        stale_mark_symbols=tuple(result.stale_mark_symbols),
        stale_mark_scope=STALE_MARK_SCOPE_LAST_BAR_ONLY,
        max_drawdown_source=MAX_DRAWDOWN_SOURCE_SIMULATED_CURVE,
    )


def _percentile(values: list[float], q: int) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    rank = (len(ordered) - 1) * (q / 100.0)
    lo = int(rank)
    hi = min(lo + 1, len(ordered) - 1)
    frac = rank - lo
    return ordered[lo] * (1.0 - frac) + ordered[hi] * frac

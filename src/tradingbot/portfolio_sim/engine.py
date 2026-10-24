"""Multi-symbol local portfolio simulation. No broker, network, or orders.

Look-ahead: decisions at bar ``t`` close use only candles with
``timestamp + tf_ms <= t + tf_ms``. Fills occur at the next bar open.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence

from tradingbot.data.ohlcv.models import Candle
from tradingbot.data.ohlcv.timeframes import timeframe_ms
from tradingbot.portfolio_sim.allocation import equal_allocation, quantity_for_buy
from tradingbot.portfolio_sim.exits import (
    buy_fill_price,
    entry_fee_and_cost,
    exit_fee_and_proceeds,
    sell_fill_price,
    stop_loss_reached,
    take_profit_reached,
)
from tradingbot.portfolio_sim.models import (
    CompletedTrade,
    OpenPosition,
    PortfolioEquityPoint,
    PortfolioSimConfig,
    PortfolioSimResult,
)
from tradingbot.portfolio_sim.ranking import is_current_closed_prefix, rank_closed_prefixes
from tradingbot.strategy.ma_crossover import MACrossover, MACrossoverParams
from tradingbot.strategy.models import CandleSeries

CandidateRankFn = Callable[[Mapping[str, CandleSeries], Sequence[str]], tuple[str, ...]]
PendingExit = tuple[str, str, int]


def _merge_pending_exits(
    carried: Sequence[PendingExit],
    new_exits: Sequence[PendingExit],
    positions: Mapping[str, OpenPosition],
) -> list[PendingExit]:
    """Keep unfilled MA exits and new signals. One row per open entry."""
    merged: list[PendingExit] = []
    seen: set[tuple[str, int]] = set()
    for symbol, reason, entry_ts in (*carried, *new_exits):
        position = positions.get(symbol)
        if position is None or position.entry_timestamp != entry_ts:
            continue
        key = (symbol, entry_ts)
        if key in seen:
            continue
        seen.add(key)
        merged.append((symbol, reason, entry_ts))
    return merged


def _index_bars(series: CandleSeries) -> dict[int, Candle]:
    return {item.timestamp: item for item in series.candles}


def _closed_prefix(
    series: CandleSeries, *, now_ms: int, timeframe: str
) -> CandleSeries | None:
    tf_ms = timeframe_ms(timeframe)
    closed = [item for item in series.candles if item.timestamp + tf_ms <= now_ms]
    if not closed:
        return None
    try:
        return CandleSeries.from_closed(
            symbol=series.symbol,
            timeframe=timeframe,
            candles=closed,
            now_ms=now_ms,
        )
    except ValueError:
        return None


def _build_prefixes(
    series_by_symbol: Mapping[str, CandleSeries],
    symbol_order: Sequence[str],
    *,
    now_ms: int,
    timeframe: str,
) -> dict[str, CandleSeries]:
    prefixes: dict[str, CandleSeries] = {}
    for symbol in symbol_order:
        series = series_by_symbol.get(symbol)
        if series is None:
            continue
        prefix = _closed_prefix(series, now_ms=now_ms, timeframe=timeframe)
        if prefix is not None:
            prefixes[symbol] = prefix
    return prefixes


def run_portfolio_sim(
    *,
    series_by_symbol: Mapping[str, CandleSeries],
    symbol_order: Sequence[str],
    config: PortfolioSimConfig,
    rank_fn: CandidateRankFn | None = None,
    ma_strategy: MACrossover | None = None,
) -> PortfolioSimResult:
    """Simulate a shared-cash book of at most five longs. Next-open fills only."""
    if not symbol_order:
        raise ValueError("symbol_order must not be empty")
    if not series_by_symbol:
        raise ValueError("series_by_symbol must not be empty")
    for symbol, series in series_by_symbol.items():
        if series.timeframe != config.timeframe:
            raise ValueError(
                f"series timeframe {series.timeframe!r} != config {config.timeframe!r}"
            )
        if series.symbol != symbol:
            raise ValueError(f"series symbol {series.symbol!r} != key {symbol!r}")

    strategy = ma_strategy or MACrossover(MACrossoverParams(fast=20, slow=50))
    tf_ms = timeframe_ms(config.timeframe)
    rate = config.commission_rate
    slip = config.slippage
    bars = {symbol: _index_bars(series) for symbol, series in series_by_symbol.items()}
    timestamps = sorted({ts for index in bars.values() for ts in index})
    if not timestamps:
        raise ValueError("no candles to simulate")

    cash = float(config.initial_cash)
    positions: dict[str, OpenPosition] = {}
    trades: list[CompletedTrade] = []
    equity: list[PortfolioEquityPoint] = []
    pending_exits: list[PendingExit] = []
    pending_buys: tuple[str, ...] = ()
    last_close: dict[str, tuple[int, float]] = {}
    max_open = 0

    def _mark_price(symbol: str, ts: int) -> float | None:
        candle = bars.get(symbol, {}).get(ts)
        if candle is not None:
            return candle.close
        marked = last_close.get(symbol)
        if marked is None or marked[0] > ts:
            return None
        return marked[1]

    def _sell(symbol: str, reference_sell: float, timestamp: int, reason: str) -> bool:
        nonlocal cash
        position = positions.get(symbol)
        if position is None:
            return False
        sell_fill = sell_fill_price(reference_sell, slip)
        exit_fee, proceeds = exit_fee_and_proceeds(
            quantity=position.quantity, sell_fill=sell_fill, commission_rate=rate
        )
        pnl = proceeds - position.entry_cost
        slip_cost = position.quantity * (position.buy_fill - position.reference_buy)
        slip_cost += position.quantity * (reference_sell - sell_fill)
        return_pct = (pnl / position.entry_cost * 100.0) if position.entry_cost else 0.0
        trades.append(
            CompletedTrade(
                symbol=symbol,
                entry_timestamp=position.entry_timestamp,
                exit_timestamp=timestamp,
                buy_fill=position.buy_fill,
                sell_fill=sell_fill,
                reference_buy=position.reference_buy,
                reference_sell=reference_sell,
                quantity=position.quantity,
                entry_fee=position.entry_fee,
                exit_fee=exit_fee,
                pnl=pnl,
                return_pct=return_pct,
                exit_reason=reason,
                slippage_cost=slip_cost,
            )
        )
        cash += proceeds
        del positions[symbol]
        return True

    def _buy(symbol: str, reference_buy: float, timestamp: int, allocation: float) -> bool:
        nonlocal cash
        if symbol in positions:
            return False
        if len(positions) >= config.max_positions:
            return False
        if allocation <= 0 or cash <= 0:
            return False
        buy_fill = buy_fill_price(reference_buy, slip)
        qty = quantity_for_buy(
            allocation=allocation, buy_fill=buy_fill, commission_rate=rate
        )
        if qty <= 0:
            return False
        fee, cost = entry_fee_and_cost(
            quantity=qty, buy_fill=buy_fill, commission_rate=rate
        )
        if cost > cash + 1e-12:
            return False
        cash -= cost
        if cash < 0:
            cash = 0.0
        positions[symbol] = OpenPosition(
            symbol=symbol,
            quantity=qty,
            entry_timestamp=timestamp,
            buy_fill=buy_fill,
            reference_buy=reference_buy,
            entry_fee=fee,
            entry_cost=cost,
        )
        return True

    for index, ts in enumerate(timestamps):
        has_next = index < len(timestamps) - 1
        opened = bars

        exited_this_open: set[str] = set()
        remaining_exits: list[PendingExit] = []
        for symbol, reason, entry_ts in pending_exits:
            candle = opened.get(symbol, {}).get(ts)
            if candle is None:
                remaining_exits.append((symbol, reason, entry_ts))
                continue
            position = positions.get(symbol)
            if position is None or position.entry_timestamp != entry_ts:
                continue
            if _sell(symbol, candle.open, ts, reason):
                exited_this_open.add(symbol)
        pending_exits = remaining_exits

        for symbol in list(positions):
            if symbol in exited_this_open:
                continue
            candle = opened.get(symbol, {}).get(ts)
            if candle is None:
                continue
            sell_fill = sell_fill_price(candle.open, slip)
            if config.stop_loss_multiple is not None and stop_loss_reached(
                entry_cost=positions[symbol].entry_cost,
                quantity=positions[symbol].quantity,
                sell_fill=sell_fill,
                commission_rate=rate,
                stop_loss_multiple=config.stop_loss_multiple,
            ):
                _sell(symbol, candle.open, ts, "stop_loss")
                exited_this_open.add(symbol)
                continue
            if config.net_profit_multiple is not None and take_profit_reached(
                entry_cost=positions[symbol].entry_cost,
                quantity=positions[symbol].quantity,
                sell_fill=sell_fill,
                commission_rate=rate,
                net_profit_multiple=config.net_profit_multiple,
            ):
                _sell(symbol, candle.open, ts, "take_profit")
                exited_this_open.add(symbol)

        planned_buys = pending_buys
        executable = [
            symbol
            for symbol in planned_buys
            if symbol not in positions
            and symbol not in exited_this_open
            and ts in opened.get(symbol, {})
        ]
        n_new = len(planned_buys)
        alloc = equal_allocation(cash, n_new)
        for symbol in executable:
            candle = opened[symbol][ts]
            _buy(symbol, candle.open, ts, alloc)
        pending_buys = ()

        for symbol, series in series_by_symbol.items():
            candle = opened.get(symbol, {}).get(ts)
            if candle is not None:
                last_close[symbol] = (ts, candle.close)

        position_value = 0.0
        for symbol, position in positions.items():
            mark = _mark_price(symbol, ts)
            if mark is None:
                continue
            position_value += position.quantity * mark
        equity_value = cash + position_value
        cash_ratio = (cash / equity_value) if equity_value else 1.0
        equity.append(
            PortfolioEquityPoint(
                timestamp=ts,
                cash=cash,
                position_value=position_value,
                equity=equity_value,
                open_count=len(positions),
                cash_ratio=cash_ratio,
            )
        )
        max_open = max(max_open, len(positions))

        if not has_next:
            continue

        now_ms = ts + tf_ms
        prefixes = _build_prefixes(
            series_by_symbol, symbol_order, now_ms=now_ms, timeframe=config.timeframe
        )
        current = {
            symbol: prefix
            for symbol, prefix in prefixes.items()
            if is_current_closed_prefix(
                prefix, now_ms=now_ms, timeframe=config.timeframe
            )
        }
        if rank_fn is None:
            ranked = rank_closed_prefixes(
                prefixes, symbol_order, now_ms=now_ms, timeframe=config.timeframe
            )
        else:
            ranked = rank_fn(current, symbol_order)
        ma_exits: list[PendingExit] = []
        for symbol, position in positions.items():
            prefix = prefixes.get(symbol)
            if prefix is None:
                continue
            signals = strategy.generate(prefix)
            if not signals:
                continue
            last = signals[-1]
            if last.timestamp != ts or last.side != "sell":
                continue
            ma_exits.append((symbol, "ma_sell", position.entry_timestamp))
        pending_exits = _merge_pending_exits(pending_exits, ma_exits, positions)
        slots = config.max_positions - len(positions)
        buys: list[str] = []
        if slots > 0:
            for symbol in ranked:
                if symbol in positions:
                    continue
                if symbol not in current:
                    continue
                buys.append(symbol)
                if len(buys) >= slots:
                    break
        pending_buys = tuple(buys)

    last_ts = timestamps[-1]
    stale_mark_symbols = tuple(
        sorted(
            symbol
            for symbol in positions
            if last_ts not in bars.get(symbol, {})
        )
    )
    return PortfolioSimResult(
        trades=tuple(trades),
        equity=tuple(equity),
        open_positions=tuple(positions.values()),
        last_marks={symbol: price for symbol, (_ts, price) in last_close.items()},
        stale_mark_symbols=stale_mark_symbols,
        final_cash=cash,
        final_equity=equity[-1].equity if equity else cash,
        config=config,
        metadata={
            "timeframe": config.timeframe,
            "initial_cash": config.initial_cash,
            "commission_rate": rate,
            "slippage": slip,
            "slippage_label": config.slippage_label(),
            "max_positions": config.max_positions,
            "bar_count": len(timestamps),
            "max_open_positions": max_open,
            "survivorship_warning": (
                "benchmarks using the current universe on past dates may include "
                "survivorship bias; no point-in-time universe snapshots are stored"
            ),
            "stale_mark_warning": (
                "open symbols without a bar at the last timestamp are marked at "
                "the last known closed close, not a current market print"
            ),
            "stale_open_symbols": ",".join(stale_mark_symbols),
        },
    )

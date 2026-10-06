from __future__ import annotations

from collections.abc import Sequence

from tradingbot.backtest.models import (
    BacktestConfig,
    BacktestResult,
    EquityPoint,
    Trade,
)
from tradingbot.strategy.models import CandleSeries, Signal

_VALID_SIDES = frozenset({"buy", "sell", "hold"})


def _validate_inputs(
    series: CandleSeries,
    signals: Sequence[Signal],
) -> None:
    candles = series.candles
    if not candles:
        raise ValueError("empty series")
    if len(signals) != len(candles):
        raise ValueError("signal length mismatch")
    for index, signal in enumerate(signals):
        if signal.symbol != series.symbol:
            raise ValueError("signal symbol mismatch")
        if signal.timeframe != series.timeframe:
            raise ValueError("signal timeframe mismatch")
        if signal.timestamp != candles[index].timestamp:
            raise ValueError("signal timestamp mismatch")
        if signal.side not in _VALID_SIDES:
            raise ValueError(f"invalid signal side {signal.side!r}")


def _actionable(side: str, quantity: float) -> bool:
    if side == "buy" and quantity == 0:
        return True
    if side == "sell" and quantity > 0:
        return True
    return False


def run_backtest(
    series: CandleSeries,
    signals: Sequence[Signal],
    config: BacktestConfig,
) -> BacktestResult:
    _validate_inputs(series, signals)
    candles = series.candles
    rate = config.commission_rate
    cash = float(config.initial_cash)
    quantity = 0.0
    entry_timestamp: int | None = None
    entry_price: float | None = None
    entry_fee: float | None = None
    trades: list[Trade] = []
    equity: list[EquityPoint] = []
    pending: Signal | None = None
    unfilled_signal: Signal | None = None

    def apply_fill(signal: Signal, fill_candle) -> None:
        nonlocal cash, quantity, entry_timestamp, entry_price, entry_fee
        price = fill_candle.open
        if signal.side == "buy":
            if quantity > 0:
                return
            qty = cash / (price * (1.0 + rate))
            fee = qty * price * rate
            cash = cash - qty * price - fee
            cash = max(0.0, cash)
            quantity = qty
            entry_timestamp = fill_candle.timestamp
            entry_price = price
            entry_fee = fee
            return
        if signal.side == "sell":
            if quantity <= 0:
                return
            assert entry_timestamp is not None
            assert entry_price is not None
            assert entry_fee is not None
            gross = quantity * price
            exit_fee = gross * rate
            net = gross - exit_fee
            entry_cost = entry_price * quantity + entry_fee
            pnl = net - entry_cost
            return_pct = (pnl / entry_cost * 100.0) if entry_cost != 0 else 0.0
            trades.append(
                Trade(
                    symbol=series.symbol,
                    entry_timestamp=entry_timestamp,
                    exit_timestamp=fill_candle.timestamp,
                    entry_price=entry_price,
                    exit_price=price,
                    quantity=quantity,
                    entry_fee=entry_fee,
                    exit_fee=exit_fee,
                    pnl=pnl,
                    return_pct=return_pct,
                )
            )
            cash += net
            quantity = 0.0
            entry_timestamp = None
            entry_price = None
            entry_fee = None

    last_index = len(candles) - 1
    for index, candle in enumerate(candles):
        if pending is not None:
            apply_fill(pending, candle)
            pending = None
        position_value = quantity * candle.close
        equity.append(
            EquityPoint(
                timestamp=candle.timestamp,
                cash=cash,
                quantity=quantity,
                position_value=position_value,
                equity=cash + position_value,
            )
        )
        signal = signals[index]
        if index < last_index:
            if _actionable(signal.side, quantity):
                pending = signal
        elif _actionable(signal.side, quantity):
            unfilled_signal = signal

    last = equity[-1]
    return BacktestResult(
        trades=tuple(trades),
        equity=tuple(equity),
        final_equity=last.equity,
        final_cash=last.cash,
        final_quantity=last.quantity,
        unfilled_signal=unfilled_signal,
        metadata={
            "symbol": series.symbol,
            "timeframe": series.timeframe,
            "initial_cash": config.initial_cash,
            "commission_rate": config.commission_rate,
            "bar_count": len(candles),
        },
    )

from __future__ import annotations

from dataclasses import dataclass

from tradingbot.backtest.models import BacktestResult, Trade
from tradingbot.strategy.models import CandleSeries


@dataclass(frozen=True)
class PerformanceReport:
    initial_equity: float
    final_equity: float
    total_return: float
    max_drawdown: float
    max_drawdown_pct: float
    trade_count: int
    win_count: int
    loss_count: int
    breakeven_count: int
    win_rate: float | None
    gross_profit: float
    gross_loss: float
    profit_factor: float | None
    average_trade_pnl: float | None
    average_win: float | None
    average_loss: float | None
    exposure: float
    benchmark_return: float


def _validate_alignment(result: BacktestResult, series: CandleSeries) -> None:
    equity = result.equity
    candles = series.candles
    if not equity or not candles:
        raise ValueError("empty equity or candle series")
    if len(equity) != len(candles):
        raise ValueError("equity/candle length mismatch")
    for point, candle in zip(equity, candles, strict=True):
        if point.timestamp != candle.timestamp:
            raise ValueError("equity/candle timestamp mismatch")


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


def _trade_fields(trades: tuple[Trade, ...]) -> tuple[
    int,
    int,
    int,
    int,
    float | None,
    float,
    float,
    float | None,
    float | None,
    float | None,
    float | None,
]:
    trade_count = len(trades)
    wins = [item.pnl for item in trades if item.pnl > 0]
    losses = [item.pnl for item in trades if item.pnl < 0]
    win_count = len(wins)
    loss_count = len(losses)
    breakeven_count = sum(1 for item in trades if item.pnl == 0)
    gross_profit = float(sum(wins))
    gross_loss = float(-sum(losses))
    win_rate = win_count / trade_count if trade_count else None
    average_trade_pnl = (
        sum(item.pnl for item in trades) / trade_count if trade_count else None
    )
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else None
    average_win = sum(wins) / win_count if win_count else None
    average_loss = sum(losses) / loss_count if loss_count else None
    return (
        trade_count,
        win_count,
        loss_count,
        breakeven_count,
        win_rate,
        gross_profit,
        gross_loss,
        profit_factor,
        average_trade_pnl,
        average_win,
        average_loss,
    )


def analyze_performance(
    result: BacktestResult,
    series: CandleSeries,
) -> PerformanceReport:
    _validate_alignment(result, series)
    initial_equity = result.equity[0].equity
    if initial_equity == 0:
        raise ValueError("initial equity is zero")
    final_equity = result.final_equity
    max_dd, max_dd_pct = _max_drawdown(tuple(point.equity for point in result.equity))
    (
        trade_count,
        win_count,
        loss_count,
        breakeven_count,
        win_rate,
        gross_profit,
        gross_loss,
        profit_factor,
        average_trade_pnl,
        average_win,
        average_loss,
    ) = _trade_fields(result.trades)
    exposed = sum(1 for point in result.equity if point.quantity > 0)
    first_close = series.candles[0].close
    last_close = series.candles[-1].close
    return PerformanceReport(
        initial_equity=initial_equity,
        final_equity=final_equity,
        total_return=(final_equity - initial_equity) / initial_equity,
        max_drawdown=max_dd,
        max_drawdown_pct=max_dd_pct,
        trade_count=trade_count,
        win_count=win_count,
        loss_count=loss_count,
        breakeven_count=breakeven_count,
        win_rate=win_rate,
        gross_profit=gross_profit,
        gross_loss=gross_loss,
        profit_factor=profit_factor,
        average_trade_pnl=average_trade_pnl,
        average_win=average_win,
        average_loss=average_loss,
        exposure=exposed / len(result.equity),
        benchmark_return=last_close / first_close - 1.0,
    )

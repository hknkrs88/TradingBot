"""Offline backtest simulation. No broker, no network, no orders."""

from tradingbot.backtest.dataset import BacktestDataset, load_backtest_series
from tradingbot.backtest.engine import run_backtest
from tradingbot.backtest.models import (
    BacktestConfig,
    BacktestResult,
    EquityPoint,
    Trade,
)
from tradingbot.backtest.performance import PerformanceReport, analyze_performance

__all__ = [
    "BacktestConfig",
    "BacktestDataset",
    "BacktestResult",
    "EquityPoint",
    "PerformanceReport",
    "Trade",
    "analyze_performance",
    "load_backtest_series",
    "run_backtest",
]

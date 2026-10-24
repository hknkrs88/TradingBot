"""Local multi-symbol portfolio simulation. No broker, network, or orders."""

from tradingbot.portfolio_sim.engine import run_portfolio_sim
from tradingbot.portfolio_sim.models import (
    CompletedTrade,
    OpenPosition,
    OpenPositionMark,
    PortfolioEquityPoint,
    PortfolioSimConfig,
    PortfolioSimResult,
    slippage_from_bps,
)
from tradingbot.portfolio_sim.performance import (
    PortfolioPerformanceReport,
    analyze_portfolio,
)

__all__ = [
    "CompletedTrade",
    "OpenPosition",
    "OpenPositionMark",
    "PortfolioEquityPoint",
    "PortfolioPerformanceReport",
    "PortfolioSimConfig",
    "PortfolioSimResult",
    "analyze_portfolio",
    "run_portfolio_sim",
    "slippage_from_bps",
]

"""Offline indicator research. No orders, scoring, simulation, or sync."""

from tradingbot.research.forward import HORIZONS, forward_returns
from tradingbot.research.spearman import SpearmanResult, spearman_corr

__all__ = [
    "HORIZONS",
    "SpearmanResult",
    "forward_returns",
    "spearman_corr",
]

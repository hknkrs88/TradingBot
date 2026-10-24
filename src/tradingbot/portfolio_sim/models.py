"""Portfolio simulation records. Local only; no broker or orders."""

from __future__ import annotations

from dataclasses import dataclass

from tradingbot.data.ohlcv.timeframes import timeframe_ms

SLIPPAGE_SCENARIO_BPS: tuple[int, ...] = (0, 2, 5, 10)
BPS = 0.0001
NET_PROFIT_MULTIPLE = 1.01
STOP_LOSS_MULTIPLE = 0.97
MAX_POSITIONS = 5


def slippage_from_bps(bps: int) -> float:
    """Convert basis points to a fraction. 1 bps = 0.0001. Not measured spread."""
    if bps < 0:
        raise ValueError("slippage bps must be >= 0")
    return float(bps) * BPS


@dataclass(frozen=True)
class PortfolioSimConfig:
    """Caller must supply cash and commission. No silent production defaults.

    ``slippage`` is a one-way sensitivity scenario, not observed book data.
    """

    initial_cash: float
    commission_rate: float
    slippage: float = 0.0
    max_positions: int = MAX_POSITIONS
    net_profit_multiple: float | None = NET_PROFIT_MULTIPLE
    stop_loss_multiple: float | None = None
    timeframe: str = "4h"

    def __post_init__(self) -> None:
        if self.initial_cash <= 0:
            raise ValueError("initial_cash must be > 0")
        if self.commission_rate < 0 or self.commission_rate >= 1:
            raise ValueError("commission_rate must be in [0, 1)")
        if self.slippage < 0 or self.slippage >= 1:
            raise ValueError("slippage must be in [0, 1)")
        if self.max_positions < 1:
            raise ValueError("max_positions must be >= 1")
        if self.net_profit_multiple is not None and self.net_profit_multiple <= 1:
            raise ValueError("net_profit_multiple must be > 1 or None")
        if self.stop_loss_multiple is not None and not (0 < self.stop_loss_multiple < 1):
            raise ValueError("stop_loss_multiple must be in (0, 1) or None")
        timeframe_ms(self.timeframe)

    def slippage_label(self) -> str:
        bps = int(round(self.slippage / BPS)) if self.slippage else 0
        return f"{bps} bps one-way slippage scenario (not measured market data)"


@dataclass(frozen=True)
class OpenPosition:
    symbol: str
    quantity: float
    entry_timestamp: int
    buy_fill: float
    reference_buy: float
    entry_fee: float
    entry_cost: float


@dataclass(frozen=True)
class CompletedTrade:
    symbol: str
    entry_timestamp: int
    exit_timestamp: int
    buy_fill: float
    sell_fill: float
    reference_buy: float
    reference_sell: float
    quantity: float
    entry_fee: float
    exit_fee: float
    pnl: float
    return_pct: float
    exit_reason: str
    slippage_cost: float


@dataclass(frozen=True)
class PortfolioEquityPoint:
    timestamp: int
    cash: float
    position_value: float
    equity: float
    open_count: int
    cash_ratio: float


@dataclass(frozen=True)
class OpenPositionMark:
    """Mark-to-market of an open position. ``stale`` means last known close, not a live print."""

    symbol: str
    quantity: float
    entry_cost: float
    mark_price: float
    mark_value: float
    unrealized_pnl: float
    hypothetical_exit_net: float
    stale: bool


@dataclass(frozen=True)
class PortfolioSimResult:
    trades: tuple[CompletedTrade, ...]
    equity: tuple[PortfolioEquityPoint, ...]
    open_positions: tuple[OpenPosition, ...]
    last_marks: dict[str, float]
    stale_mark_symbols: tuple[str, ...]
    final_cash: float
    final_equity: float
    config: PortfolioSimConfig
    metadata: dict[str, str | int | float]

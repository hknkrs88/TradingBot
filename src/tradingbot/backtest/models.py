from __future__ import annotations

from dataclasses import dataclass

from tradingbot.strategy.models import Signal


@dataclass(frozen=True)
class BacktestConfig:
    initial_cash: float
    commission_rate: float

    def __post_init__(self) -> None:
        if self.initial_cash <= 0:
            raise ValueError("initial_cash must be > 0")
        if self.commission_rate < 0 or self.commission_rate >= 1:
            raise ValueError("commission_rate must be in [0, 1)")


@dataclass(frozen=True)
class Trade:
    symbol: str
    entry_timestamp: int
    exit_timestamp: int
    entry_price: float
    exit_price: float
    quantity: float
    entry_fee: float
    exit_fee: float
    pnl: float
    return_pct: float


@dataclass(frozen=True)
class EquityPoint:
    timestamp: int
    cash: float
    quantity: float
    position_value: float
    equity: float


@dataclass(frozen=True)
class BacktestResult:
    trades: tuple[Trade, ...]
    equity: tuple[EquityPoint, ...]
    final_equity: float
    final_cash: float
    final_quantity: float
    unfilled_signal: Signal | None
    metadata: dict[str, str | int | float]

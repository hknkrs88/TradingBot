"""Research indicator records. No orders, scoring, or simulation."""

from __future__ import annotations

import math
from dataclasses import dataclass

from tradingbot.data.ohlcv.timeframes import timeframe_ms

SKIP_WARMUP = "warmup"
SKIP_GAP = "gap"
SKIP_INSUFFICIENT_HISTORY = "insufficient_history"
SKIP_NON_FINITE = "non_finite"
SKIP_UNDEFINED = "undefined"
SKIP_NOT_ALIGNED = "not_aligned"

SKIP_REASONS: frozenset[str] = frozenset(
    {
        SKIP_WARMUP,
        SKIP_GAP,
        SKIP_INSUFFICIENT_HISTORY,
        SKIP_NON_FINITE,
        SKIP_UNDEFINED,
        SKIP_NOT_ALIGNED,
    }
)


@dataclass(frozen=True)
class IndicatorPoint:
    """One timestamp: a finite value or a skip reason, never both."""

    timestamp: int
    value: float | None
    skip_reason: str | None = None

    def __post_init__(self) -> None:
        has_value = self.value is not None
        has_skip = self.skip_reason is not None
        if has_value == has_skip:
            raise ValueError("point must have exactly one of value or skip_reason")
        if has_skip and self.skip_reason not in SKIP_REASONS:
            raise ValueError(f"unknown skip_reason {self.skip_reason!r}")
        if has_value and not math.isfinite(self.value):
            raise ValueError("value must be finite")


@dataclass(frozen=True)
class IndicatorSeries:
    """Named, time-sorted indicator points for one symbol and timeframe."""

    name: str
    symbol: str
    timeframe: str
    points: tuple[IndicatorPoint, ...]

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("name must be non-empty")
        if not self.symbol:
            raise ValueError("symbol must be non-empty")
        timeframe_ms(self.timeframe)
        if not isinstance(self.points, tuple):
            raise ValueError("points must be a tuple")
        stamps = [item.timestamp for item in self.points]
        if len(stamps) != len(set(stamps)):
            raise ValueError("duplicate indicator timestamp")
        if stamps != sorted(stamps):
            raise ValueError("points must be sorted by timestamp")

from __future__ import annotations

from dataclasses import dataclass

from tradingbot.strategy.models import CandleSeries, Signal


@dataclass(frozen=True)
class MACrossoverParams:
    fast: int
    slow: int

    def __post_init__(self) -> None:
        if self.fast <= 0 or self.slow <= 0:
            raise ValueError("fast and slow must be positive")
        if self.fast >= self.slow:
            raise ValueError("fast must be < slow")


def _sma(closes: list[float], index: int, period: int) -> float:
    start = index - period + 1
    window = closes[start : index + 1]
    return sum(window) / period


def _reason(
    *,
    fast_ma: float,
    slow_ma: float,
    prev_fast: float | None,
    prev_slow: float | None,
) -> str:
    if fast_ma > slow_ma:
        if prev_fast is not None and prev_slow is not None and prev_fast <= prev_slow:
            return "cross_up"
        return "fast_above_slow"
    if fast_ma < slow_ma:
        if prev_fast is not None and prev_slow is not None and prev_fast >= prev_slow:
            return "cross_down"
        return "fast_below_slow"
    return "equal"


@dataclass(frozen=True)
class MACrossover:
    params: MACrossoverParams
    name: str = "ma_crossover"

    def generate(self, series: CandleSeries) -> list[Signal]:
        candles = series.candles
        closes = [item.close for item in candles]
        fast = self.params.fast
        slow = self.params.slow
        signals: list[Signal] = []
        prev_fast: float | None = None
        prev_slow: float | None = None
        for index, candle in enumerate(candles):
            if index < slow - 1:
                signals.append(
                    Signal(
                        symbol=series.symbol,
                        timeframe=series.timeframe,
                        timestamp=candle.timestamp,
                        side="hold",
                        strategy=self.name,
                        reason="warmup",
                    )
                )
                continue
            fast_ma = _sma(closes, index, fast)
            slow_ma = _sma(closes, index, slow)
            if fast_ma > slow_ma:
                side = "buy"
            elif fast_ma < slow_ma:
                side = "sell"
            else:
                side = "hold"
            signals.append(
                Signal(
                    symbol=series.symbol,
                    timeframe=series.timeframe,
                    timestamp=candle.timestamp,
                    side=side,
                    strategy=self.name,
                    reason=_reason(
                        fast_ma=fast_ma,
                        slow_ma=slow_ma,
                        prev_fast=prev_fast,
                        prev_slow=prev_slow,
                    ),
                )
            )
            prev_fast = fast_ma
            prev_slow = slow_ma
        return signals

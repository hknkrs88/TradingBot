from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from tradingbot.data.ohlcv.models import Candle
from tradingbot.data.ohlcv.quality import is_incomplete
from tradingbot.data.ohlcv.timeframes import timeframe_ms

Side = str


@dataclass(frozen=True)
class CandleSeries:
    symbol: str
    timeframe: str
    candles: tuple[Candle, ...]

    def __post_init__(self) -> None:
        timeframe_ms(self.timeframe)
        if not isinstance(self.candles, tuple):
            raise ValueError("candles must be a tuple")
        stamps = [item.timestamp for item in self.candles]
        if len(stamps) != len(set(stamps)):
            raise ValueError("duplicate candle timestamp")
        if stamps != sorted(stamps):
            raise ValueError("candles must be sorted by timestamp")

    @classmethod
    def from_closed(
        cls,
        *,
        symbol: str,
        timeframe: str,
        candles: Sequence[Candle],
        now_ms: int,
    ) -> CandleSeries:
        tf_ms = timeframe_ms(timeframe)
        packed = tuple(candles)
        for candle in packed:
            if is_incomplete(candle, tf_ms, now_ms):
                raise ValueError(
                    f"incomplete candle timestamp={candle.timestamp}"
                )
        return cls(symbol=symbol, timeframe=timeframe, candles=packed)


@dataclass(frozen=True)
class Signal:
    symbol: str
    timeframe: str
    timestamp: int
    side: Side
    strategy: str
    reason: str

"""Forward simple returns on closed candles. Does not fill gaps or leak future bars."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from tradingbot.data.ohlcv.models import Candle
from tradingbot.data.ohlcv.timeframes import timeframe_ms
from tradingbot.strategy.models import CandleSeries

HORIZONS: tuple[int, ...] = (1, 3, 6, 12)


def forward_returns(
    series: CandleSeries,
    horizons: Sequence[int] = HORIZONS,
) -> dict[int, tuple[tuple[int, float], ...]]:
    """``close[t+n] / close[t] - 1`` when ``timestamp[t+n] == timestamp[t] + n * tf``.

    The last ``n`` bars have no return. Gaps are not filled: a missing slot drops
    that pair instead of using the next available close.
    """
    tf_ms = timeframe_ms(series.timeframe)
    candles = series.candles
    out: dict[int, tuple[tuple[int, float], ...]] = {}
    for horizon in horizons:
        if isinstance(horizon, bool) or not isinstance(horizon, int) or horizon < 1:
            raise ValueError("horizon must be an integer >= 1")
        pairs: list[tuple[int, float]] = []
        limit = len(candles) - horizon
        for index in range(max(limit, 0)):
            current = candles[index]
            future = candles[index + horizon]
            if future.timestamp - current.timestamp != horizon * tf_ms:
                continue
            if current.close <= 0.0 or future.close <= 0.0:
                continue
            pairs.append((current.timestamp, future.close / current.close - 1.0))
        out[horizon] = tuple(pairs)
    return out


def close_by_timestamp(candles: Sequence[Candle]) -> Mapping[int, float]:
    return {item.timestamp: item.close for item in candles}

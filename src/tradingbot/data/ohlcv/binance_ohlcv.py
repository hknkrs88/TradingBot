from __future__ import annotations

from typing import Any

import ccxt

from tradingbot.data.binance_markets import create_public_binance
from tradingbot.data.ohlcv.models import Candle
from tradingbot.data.ohlcv.timeframes import timeframe_ms

MAX_OHLCV_LIMIT = 1000


class BinancePublicOHLCVProvider:
    """Unauthenticated live Spot klines. Not the paper/testnet trading client."""

    def __init__(self, exchange: ccxt.binance | None = None) -> None:
        self.exchange = exchange or create_public_binance()

    def fetch_ohlcv(
        self,
        symbol: str,
        timeframe: str,
        *,
        since: int | None = None,
        limit: int | None = None,
    ) -> list[Candle]:
        timeframe_ms(timeframe)
        page_limit = MAX_OHLCV_LIMIT if limit is None else min(max(int(limit), 1), MAX_OHLCV_LIMIT)
        rows: list[Any] = self.exchange.fetch_ohlcv(
            symbol, timeframe, since=since, limit=page_limit
        )
        return [Candle.from_ccxt(row) for row in rows]

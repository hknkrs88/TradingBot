from __future__ import annotations

from typing import Protocol

from tradingbot.data.ohlcv.models import Candle


class OHLCVProvider(Protocol):
    def fetch_ohlcv(
        self,
        symbol: str,
        timeframe: str,
        *,
        since: int | None = None,
        limit: int | None = None,
    ) -> list[Candle]:
        """Return candles. No private endpoints; no orders."""

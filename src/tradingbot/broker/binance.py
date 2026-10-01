from __future__ import annotations

import ccxt

from tradingbot.config import Settings


class LiveTradingDisabledError(RuntimeError):
    """Raised when the process would talk to live Binance."""


def create_binance_exchange(settings: Settings) -> ccxt.binance:
    if settings.mode != "paper":
        raise LiveTradingDisabledError(
            "live trading is disabled; use mode=paper and Binance testnet"
        )
    if not settings.sandbox:
        raise LiveTradingDisabledError("sandbox/testnet is required")

    exchange = ccxt.binance(
        {
            "apiKey": settings.binance_api_key or None,
            "secret": settings.binance_api_secret or None,
            "enableRateLimit": True,
            "options": {
                "defaultType": settings.market_type,
                "adjustForTimeDifference": True,
            },
        }
    )
    exchange.set_sandbox_mode(True)
    return exchange

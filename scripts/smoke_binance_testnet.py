"""Manual connectivity check against Binance spot testnet. Does not place orders."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from tradingbot.broker.binance import create_binance_exchange
from tradingbot.config import load_settings


def main() -> int:
    settings = load_settings(ROOT / "configs" / "paper.toml")
    exchange = create_binance_exchange(settings)
    ticker = exchange.fetch_ticker(settings.symbol)
    print(settings.symbol, "last=", ticker.get("last"), "bid=", ticker.get("bid"), "ask=", ticker.get("ask"))
    if settings.binance_api_key:
        balance = exchange.fetch_balance()
        print("free USDT=", balance.get("USDT", {}).get("free"))
    else:
        print("no API keys; skipped fetch_balance (public ticker only)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

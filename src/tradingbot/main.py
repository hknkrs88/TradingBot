from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from tradingbot.broker.binance import create_binance_exchange
from tradingbot.config import load_settings

log = logging.getLogger("tradingbot")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Trading bot (Binance testnet / paper)")
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("configs/paper.toml"),
        help="TOML config path",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    settings = load_settings(args.config)
    log.info(
        "boot mode=%s exchange=%s sandbox=%s symbol=%s",
        settings.mode,
        settings.exchange_id,
        settings.sandbox,
        settings.symbol,
    )

    exchange = create_binance_exchange(settings)
    ticker = exchange.fetch_ticker(settings.symbol)
    last = ticker.get("last")
    log.info("testnet ticker %s last=%s (no orders sent)", settings.symbol, last)
    return 0


if __name__ == "__main__":
    sys.exit(main())

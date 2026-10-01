"""Sync OHLCV for universe symbols from public Binance Spot. No orders."""

from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

from tradingbot.config import load_settings
from tradingbot.data.ohlcv.binance_ohlcv import BinancePublicOHLCVProvider
from tradingbot.data.ohlcv.sync import load_ohlcv_config, sync_ohlcv

ROOT = Path(__file__).resolve().parents[1]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Sync public OHLCV for the coin universe")
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--config", type=Path, default=None)
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    root: Path = args.root
    settings = load_settings(root / "configs" / "paper.toml")
    ohlcv_path = args.config or (root / settings.ohlcv_config_path)
    config = load_ohlcv_config(ohlcv_path)
    universe_path = root / settings.universe_path
    now_ms = int(time.time() * 1000)
    report = sync_ohlcv(
        universe_path=universe_path,
        config=config,
        provider=BinancePublicOHLCVProvider(),
        root=root,
        now_ms=now_ms,
    )
    print(
        "symbols_ok={ok} symbols_failed={fail} candles_fetched={n} cache_hits={h} cache_misses={m}".format(
            ok=report["symbols_ok"],
            fail=report["symbols_failed"],
            n=report["candles_fetched"],
            h=report["cache_hits"],
            m=report["cache_misses"],
        )
    )
    for err in report["errors"]:
        print(f"error symbol={err['symbol']} tf={err['timeframe']} reason={err['reason']}")
    return 1 if report["symbols_failed"] else 0


if __name__ == "__main__":
    sys.exit(main())

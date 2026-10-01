"""Build data/processed/universe.json from CoinGecko + live Binance public markets.

Uses one CoinGecko markets request (or cache) and unauthenticated ccxt load_markets().
Does not place orders or call private endpoints.
"""

from __future__ import annotations

import argparse
import logging
import sys
from collections import Counter
from pathlib import Path

from tradingbot.data.binance_markets import load_or_fetch_binance_markets
from tradingbot.data.coingecko import load_or_fetch_coingecko
from tradingbot.data.match_policy import load_match_policy
from tradingbot.data.stables import load_stables
from tradingbot.data.universe import build_universe_payload, write_universe_atomic

ROOT = Path(__file__).resolve().parents[1]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build coin universe (public metadata only)")
    parser.add_argument("--refresh", action="store_true", help="bypass valid caches and refetch")
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    root: Path = args.root
    gecko_path = root / "data" / "raw" / "coingecko_markets.json"
    binance_path = root / "data" / "raw" / "binance_spot_markets.json"
    universe_path = root / "data" / "processed" / "universe.json"
    stables_path = root / "configs" / "stables.toml"
    match_path = root / "configs" / "universe_match.toml"

    try:
        stables = load_stables(stables_path)
        policy = load_match_policy(match_path)
        gecko_rows = load_or_fetch_coingecko(gecko_path, refresh=args.refresh)
        markets = load_or_fetch_binance_markets(binance_path, refresh=args.refresh)
        assets, skipped, generated_at = build_universe_payload(
            gecko_rows, markets, stables, policy
        )
        write_universe_atomic(
            universe_path,
            assets,
            generated_at=generated_at,
            coingecko_raw=gecko_path,
            binance_raw=binance_path,
            skipped=skipped,
        )
    except Exception:
        logging.exception("universe build failed; existing universe.json left unchanged")
        return 1

    reasons = Counter(item["reason"] for item in skipped)
    print(f"universe_path={universe_path}")
    print(f"matched={len(assets)} skipped={len(skipped)} generated_at={generated_at}")
    print(f"skip_reasons={dict(reasons)}")
    for row in assets[:20]:
        print(f"{row['market_cap_rank']}\t{row['binance_symbol']}\t{row['coingecko_id']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

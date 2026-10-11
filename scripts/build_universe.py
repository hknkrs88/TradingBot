"""Build data/processed/universe.json from CoinGecko + live Binance public markets.

Pages CoinGecko markets (250 per page) until 200 eligible matches or MAX_GECKO_PAGES.
Uses unauthenticated ccxt load_markets(). Does not place orders or call private endpoints.
"""

from __future__ import annotations

import argparse
import logging
import sys
from collections import Counter
from pathlib import Path

from tradingbot.data.binance_markets import load_or_fetch_binance_markets
from tradingbot.data.coingecko import COINGECKO_PER_PAGE, gecko_page_cache_path
from tradingbot.data.match_policy import load_match_policy
from tradingbot.data.stables import load_stables
from tradingbot.data.universe import build_universe_from_gecko_pages, write_universe_atomic

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
    gecko_dir = root / "data" / "raw"
    gecko_path = gecko_page_cache_path(gecko_dir, page=1, per_page=COINGECKO_PER_PAGE)
    binance_path = root / "data" / "raw" / "binance_spot_markets.json"
    universe_path = root / "data" / "processed" / "universe.json"
    stables_path = root / "configs" / "stables.toml"
    match_path = root / "configs" / "universe_match.toml"

    try:
        stables = load_stables(stables_path)
        policy = load_match_policy(match_path)
        markets = load_or_fetch_binance_markets(binance_path, refresh=args.refresh)
        assets, skipped, generated_at, gecko_files = build_universe_from_gecko_pages(
            gecko_dir, markets, stables, policy, refresh=args.refresh
        )
        page1 = gecko_path if gecko_path.is_file() else gecko_files[0]
        write_universe_atomic(
            universe_path,
            assets,
            generated_at=generated_at,
            coingecko_raw=page1,
            binance_raw=binance_path,
            skipped=skipped,
            coingecko_page_files=gecko_files,
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

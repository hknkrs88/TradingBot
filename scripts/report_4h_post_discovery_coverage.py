"""Report fixed post-discovery 4h month coverage. Reads files and does not write."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from tradingbot.config import load_settings
from tradingbot.data.ohlcv.post_discovery_coverage import assess_post_discovery_coverage
from tradingbot.data.ohlcv.sync import load_ohlcv_config

ROOT = Path(__file__).resolve().parents[1]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Report closed 4h coverage for Nov 2026, Dec 2026, and Jan 2027. Does not sync or write."
    )
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--config", type=Path, default=None)
    parser.add_argument("--universe", type=Path, required=True)
    parser.add_argument("--now-ms", type=int, default=None)
    args = parser.parse_args(argv)

    root: Path = args.root
    settings = load_settings(root / "configs" / "paper.toml")
    ohlcv_path = args.config or (root / settings.ohlcv_config_path)
    config = load_ohlcv_config(ohlcv_path)
    universe_path = args.universe if args.universe.is_absolute() else root / args.universe
    report = assess_post_discovery_coverage(
        root=root,
        universe_path=universe_path,
        processed_root=root / config.processed_dir,
        now_ms=args.now_ms if args.now_ms is not None else int(time.time() * 1000),
        drop_incomplete=config.drop_incomplete,
    )
    print(json.dumps(report, indent=2, sort_keys=True))
    return 1 if report["status"] == "INVALID_DATA" else 0


if __name__ == "__main__":
    sys.exit(main())

"""Rebuild manifest_4h.json from processed files. No network and no candle writes."""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from tradingbot.config import load_settings
from tradingbot.data.ohlcv.sync import load_ohlcv_config
from tradingbot.data.ohlcv.quality import UnalignedTimestampError
from tradingbot.data.ohlcv.validation_4h import ValidationDatasetError, rebuild_4h_manifest

ROOT = Path(__file__).resolve().parents[1]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Rewrite the 4h OHLCV manifest from existing candle files. Does not sync."
    )
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--config", type=Path, default=None)
    parser.add_argument("--universe", type=Path, required=True)
    args = parser.parse_args(argv)

    root: Path = args.root
    settings = load_settings(root / "configs" / "paper.toml")
    ohlcv_path = args.config or (root / settings.ohlcv_config_path)
    config = load_ohlcv_config(ohlcv_path)
    universe_path = args.universe if args.universe.is_absolute() else root / args.universe
    try:
        written = rebuild_4h_manifest(
            root=root,
            universe_path=universe_path,
            processed_root=root / config.processed_dir,
            provider=config.provider,
            candle_limit=config.candle_limit,
            drop_incomplete=config.drop_incomplete,
            now_ms=int(time.time() * 1000),
        )
    except ValidationDatasetError as exc:
        print(f"error: {exc.reason}", file=sys.stderr)
        return 1
    except UnalignedTimestampError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"wrote {written}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

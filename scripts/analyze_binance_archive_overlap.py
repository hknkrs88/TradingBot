"""Compare experimental archive candles to processed OHLCV. Read-only; no download."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from tradingbot.data.ohlcv.archive_import import resolve_archive_series_input
from tradingbot.data.ohlcv.archive_overlap import analyze_archive_processed_files

ROOT = Path(__file__).resolve().parents[1]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Experimental archive vs processed OHLCV overlap analysis"
    )
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--timeframe", required=True, choices=["1h", "4h"])
    parser.add_argument(
        "--archive-series",
        type=Path,
        required=True,
        help=(
            "File path to experimental candles.json (used as-is). "
            "A generation directory or experimental series root with current.json "
            "is resolved via the live generation pointer. "
            "Legacy series-root candles.json without current.json is not used."
        ),
    )
    parser.add_argument(
        "--report",
        type=Path,
        required=True,
        help="Output overlap report path (must not be under data/processed)",
    )
    parser.add_argument("--mismatch-example-limit", type=int, default=20)
    args = parser.parse_args(argv)

    archive_series = args.archive_series
    if archive_series.is_dir():
        archive_series = resolve_archive_series_input(archive_series)
    report = analyze_archive_processed_files(
        root=args.root,
        symbol=args.symbol,
        timeframe=args.timeframe,
        archive_series_path=archive_series,
        report_path=args.report,
        mismatch_example_limit=args.mismatch_example_limit,
    )
    print(
        "status={status} common={n} matched={m} mismatched={x}".format(
            status=report["status"],
            n=report["counts"]["common_timestamps"],
            m=report["counts"]["matched_ohlcv_bars"],
            x=report["counts"]["mismatched_ohlcv_bars"],
        )
    )
    return 0 if report["status"] != "failed" else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc

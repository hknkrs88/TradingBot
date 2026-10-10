"""Promote experimental archive OHLCV into processed data. Dry-run unless --apply."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from tradingbot.data.ohlcv.archive_promote import ArchivePromoteRequest, promote_archive_series

ROOT = Path(__file__).resolve().parents[1]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Guarded experimental archive promotion (dry-run default)"
    )
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--timeframe", required=True, choices=["1h", "4h"])
    parser.add_argument("--archive-series", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write processed series+manifest (requires --accept-json-decimal)",
    )
    parser.add_argument(
        "--accept-json-decimal",
        action="store_true",
        help="Acknowledge JSON Decimal comparison is not original CSV precision",
    )
    args = parser.parse_args(argv)
    report = promote_archive_series(
        ArchivePromoteRequest(
            root=args.root,
            symbol=args.symbol,
            timeframe=args.timeframe,
            archive_series_path=args.archive_series,
            report_path=args.report,
            apply=args.apply,
            accept_json_decimal=args.accept_json_decimal,
        )
    )
    print(
        "status={status} files_changed={changed} reason={reason}".format(
            status=report["status"],
            changed=report["files_changed"],
            reason=report.get("failure_reason") or report.get("apply_blocked_reason") or "",
        )
    )
    return 0 if report["status"] in {"dry_run", "applied"} else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc

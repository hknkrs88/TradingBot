"""Import experimental Binance Spot archive klines. Does not touch processed OHLCV."""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime
from pathlib import Path

from tradingbot.data.ohlcv.archive_import import (
    EXPERIMENTAL_REL,
    ArchiveImportRequest,
    UrllibArchiveHttpClient,
    import_archive_klines,
)

ROOT = Path(__file__).resolve().parents[1]


def _parse_utc_ms(value: str) -> int:
    text = value.strip()
    if len(text) == 10:
        dt = datetime.strptime(text, "%Y-%m-%d").replace(tzinfo=UTC)
    else:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        dt = dt.astimezone(UTC)
    return int(dt.timestamp()) * 1000


def main(argv: list[str] | None = None, *, client=None) -> int:
    parser = argparse.ArgumentParser(
        description="Experimental Binance archive import (not processed OHLCV sync)"
    )
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--timeframe", required=True, choices=["1h", "4h"])
    parser.add_argument("--start", required=True, help="UTC start inclusive (YYYY-MM-DD)")
    parser.add_argument("--end", required=True, help="UTC end exclusive (YYYY-MM-DD)")
    parser.add_argument("--cadence", choices=["monthly", "daily"], default="monthly")
    parser.add_argument(
        "--dest-root",
        type=Path,
        default=None,
        help="Experimental output root (default: <root>/data/experimental/binance_archive)",
    )
    args = parser.parse_args(argv)

    dest_root = args.dest_root if args.dest_root is not None else args.root / EXPERIMENTAL_REL
    request = ArchiveImportRequest(
        symbol=args.symbol,
        timeframe=args.timeframe,
        start_ms=_parse_utc_ms(args.start),
        end_exclusive_ms=_parse_utc_ms(args.end),
        cadence=args.cadence,
        dest_root=dest_root,
    )
    report = import_archive_klines(request, client=client or UrllibArchiveHttpClient())
    print(
        "status={status} symbol={symbol} tf={tf} candles={n} periods={p} "
        "generation={generation} current={current} candles_path={candles_path}".format(
            status=report["status"],
            symbol=report["symbol"],
            tf=report["timeframe"],
            n=report["series"]["candle_count"],
            p=len(report["periods"]),
            generation=report.get("generation"),
            current=report.get("current_path"),
            candles_path=report.get("candles_path"),
        )
    )
    return 0 if report["status"] != "failed" else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc

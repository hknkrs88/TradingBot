"""Read-only 4h coverage for the fixed post-discovery months.

The months are the g-p-v2 window for discovery end 2026-10-11T12:00:00Z:
the first complete UTC month whose closed 4h bars, and whose h=12 closes,
lie strictly after that instant, plus the next two UTC months. They are not
derived from the system clock. This module does not write candles, manifests,
or a run record.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from tradingbot.data.io import InvalidCacheError
from tradingbot.data.ohlcv.quality import UnalignedTimestampError, is_incomplete, require_aligned_4h
from tradingbot.data.ohlcv.store import load_candles, processed_path
from tradingbot.data.ohlcv.sync import load_universe_symbols
from tradingbot.data.ohlcv.timeframes import timeframe_ms
from tradingbot.data.ohlcv.validation_4h import (
    ValidationDatasetError,
    inspect_4h_validation_dataset,
)

DISCOVERY_END = "2026-10-11T12:00:00Z"
POST_DISCOVERY_MONTHS = ((2026, 11), (2026, 12), (2027, 1))
TIMEFRAME = "4h"
TF_MS = timeframe_ms(TIMEFRAME)


def month_bounds(year: int, month: int) -> tuple[int, int]:
    """Return ``[start, next_month)`` in UTC milliseconds."""
    start = datetime(year, month, 1, tzinfo=timezone.utc)
    if month == 12:
        end = datetime(year + 1, 1, 1, tzinfo=timezone.utc)
    else:
        end = datetime(year, month + 1, 1, tzinfo=timezone.utc)
    return int(start.timestamp() * 1000), int(end.timestamp() * 1000)


def expected_slots(year: int, month: int) -> list[int]:
    start, end = month_bounds(year, month)
    return list(range(start, end, TF_MS))


def month_symbol_coverage(timestamps: list[int], year: int, month: int) -> dict[str, Any]:
    """Count closed timestamps inside one UTC month. Does not fill gaps."""
    slots = expected_slots(year, month)
    start, end = month_bounds(year, month)
    present = [item for item in timestamps if start <= item < end]
    found = set(present)
    missing = [item for item in slots if item not in found]
    return {
        "found": len(found),
        "first": min(found) if found else None,
        "last": max(found) if found else None,
        "expected": len(slots),
        "missing_count": len(missing),
        "missing_slots": missing,
    }


def _closed_timestamps(candles: list, now_ms: int) -> list[int]:
    return [
        candle.timestamp
        for candle in candles
        if not is_incomplete(candle, TF_MS, now_ms)
    ]


def _integrity_errors(symbols: list[str], processed_root: Path) -> tuple[list[dict[str, Any]], dict[str, list]]:
    errors: list[dict[str, Any]] = []
    loaded: dict[str, list] = {}
    for symbol in symbols:
        path = processed_path(processed_root, symbol, TIMEFRAME)
        if not path.is_file():
            errors.append({"code": "missing_file", "symbol": symbol})
            continue
        try:
            candles = load_candles(path)
        except InvalidCacheError:
            errors.append({"code": "unreadable_file", "symbol": symbol})
            continue
        try:
            require_aligned_4h(symbol, candles)
        except UnalignedTimestampError as exc:
            errors.append(
                {
                    "code": "unaligned_timestamp",
                    "symbol": exc.symbol,
                    "timestamp": exc.timestamp,
                }
            )
            continue
        counts: dict[int, int] = {}
        for candle in candles:
            counts[candle.timestamp] = counts.get(candle.timestamp, 0) + 1
        duplicates = sorted(ts for ts, count in counts.items() if count > 1)
        if duplicates:
            errors.append(
                {
                    "code": "duplicate_timestamp",
                    "symbol": symbol,
                    "timestamps": duplicates,
                }
            )
            continue
        loaded[symbol] = candles
    return errors, loaded


def _month_report(label: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "month": label,
        "expected_slots": rows[0]["expected"] if rows else 0,
        "total_candles": sum(row["found"] for row in rows),
        "symbols_with_data": sum(1 for row in rows if row["found"] > 0),
        "symbols_complete": sum(1 for row in rows if row["missing_count"] == 0),
        "symbols": rows,
    }


def assess_post_discovery_coverage(
    *,
    root: Path,
    universe_path: Path,
    processed_root: Path,
    now_ms: int,
    drop_incomplete: bool,
) -> dict[str, Any]:
    """Return a coverage report. Reads files and does not write them."""
    try:
        symbols = load_universe_symbols(universe_path)
    except InvalidCacheError as exc:
        return {
            "status": "INVALID_DATA",
            "discovery_end": DISCOVERY_END,
            "months": [],
            "errors": [{"code": "unreadable_universe", "message": str(exc)}],
        }

    errors, loaded = _integrity_errors(symbols, processed_root)
    if errors:
        return {
            "status": "INVALID_DATA",
            "discovery_end": DISCOVERY_END,
            "months": [],
            "errors": errors,
        }

    try:
        state = inspect_4h_validation_dataset(
            root=root,
            universe_path=universe_path,
            processed_root=processed_root,
            now_ms=now_ms,
            drop_incomplete=drop_incomplete,
        )
    except ValidationDatasetError as exc:
        return {
            "status": "INVALID_DATA",
            "discovery_end": DISCOVERY_END,
            "months": [],
            "errors": [{"code": "manifest_mismatch", "reason": exc.reason}],
        }
    except UnalignedTimestampError as exc:
        return {
            "status": "INVALID_DATA",
            "discovery_end": DISCOVERY_END,
            "months": [],
            "errors": [
                {
                    "code": "unaligned_timestamp",
                    "symbol": exc.symbol,
                    "timestamp": exc.timestamp,
                }
            ],
        }
    if state != "ready":
        return {
            "status": "INVALID_DATA",
            "discovery_end": DISCOVERY_END,
            "months": [],
            "errors": [{"code": "manifest_mismatch", "reason": state}],
        }

    months: list[dict[str, Any]] = []
    complete = True
    for year, month in POST_DISCOVERY_MONTHS:
        slots = expected_slots(year, month)
        covered: set[int] = set()
        rows = []
        for symbol in symbols:
            closed = _closed_timestamps(loaded[symbol], now_ms)
            row = month_symbol_coverage(closed, year, month)
            row["symbol"] = symbol
            covered.update(set(slots) - set(row["missing_slots"]))
            rows.append(row)
        if covered != set(slots):
            complete = False
        label = f"{year:04d}-{month:02d}"
        months.append(_month_report(label, rows))
    return {
        "status": "READY" if complete else "NOT_READY",
        "discovery_end": DISCOVERY_END,
        "months": months,
        "errors": [],
    }

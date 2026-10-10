"""Compare experimental archive candles to processed OHLCV. Read-only analysis."""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Mapping, Sequence
from decimal import Decimal
from pathlib import Path
from typing import Any

from tradingbot.data.io import atomic_write_json
from tradingbot.data.ohlcv.reader import load_processed
from tradingbot.data.ohlcv.store import processed_path, safe_symbol
from tradingbot.data.ohlcv.timeframes import timeframe_ms

OVERLAP_SCHEMA = "binance_archive_overlap_report/v1"
OHLCV_FIELDS = ("open", "high", "low", "close", "volume")
DEFAULT_EXAMPLE_LIMIT = 20
COMPARISON_METHOD = {
    "name": "json_number_decimal",
    "equality": "Decimal exact equality on JSON number tokens",
    "not_used": "Candle.float and binary float equality",
    "limitation": (
        "JSON tokens are used, not original Binance CSV strings. "
        "Imported series may already have been round-tripped through float."
    ),
}


class ArchiveOverlapError(ValueError):
    """Overlap analysis cannot produce a safe comparison."""


def load_decimal_ohlcv_file(path: Path) -> dict[str, Any]:
    """Load a dump_candles JSON file using Decimal for numeric fields."""
    if not path.is_file():
        raise ArchiveOverlapError(f"missing series file: {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"), parse_float=Decimal)
    except json.JSONDecodeError as exc:
        raise ArchiveOverlapError(f"invalid json: {path}") from exc
    if not isinstance(payload, dict):
        raise ArchiveOverlapError("ohlcv payload must be an object")
    return payload


def analyze_series_overlap(
    archive_payload: Mapping[str, Any],
    processed_payload: Mapping[str, Any],
    *,
    symbol: str,
    timeframe: str,
    archive_path: str,
    processed_path_str: str,
    mismatch_example_limit: int = DEFAULT_EXAMPLE_LIMIT,
) -> dict[str, Any]:
    tf_ms = timeframe_ms(timeframe)
    wanted = safe_symbol(symbol)
    archive_tf = str(archive_payload.get("timeframe") or "")
    processed_tf = str(processed_payload.get("timeframe") or "")
    if archive_tf != timeframe or processed_tf != timeframe:
        raise ArchiveOverlapError(
            f"timeframe mismatch: archive={archive_tf!r} processed={processed_tf!r} "
            f"expected={timeframe!r}"
        )
    archive_symbol = safe_symbol(str(archive_payload.get("symbol") or wanted))
    processed_symbol = safe_symbol(str(processed_payload.get("symbol") or wanted))
    if archive_symbol != wanted or processed_symbol != wanted:
        raise ArchiveOverlapError(
            f"symbol mismatch: archive={archive_symbol!r} processed={processed_symbol!r} "
            f"expected={wanted!r}"
        )

    archive_rows = _decimal_bars(archive_payload.get("candles"), "archive")
    processed_rows = _decimal_bars(processed_payload.get("candles"), "processed")
    warnings: list[str] = []
    archive_dups = _duplicate_timestamps(archive_rows)
    processed_dups = _duplicate_timestamps(processed_rows)
    if archive_dups or processed_dups:
        return _failed_report(
            symbol=wanted,
            timeframe=timeframe,
            archive_path=archive_path,
            processed_path_str=processed_path_str,
            archive_rows=archive_rows,
            processed_rows=processed_rows,
            archive_dups=archive_dups,
            processed_dups=processed_dups,
            warnings=["duplicate timestamps; no bar was selected"],
            reason="duplicate_timestamps",
        )

    archive_rows = sorted(archive_rows, key=lambda row: row["timestamp"])
    processed_rows = sorted(processed_rows, key=lambda row: row["timestamp"])
    _require_aligned(archive_rows, tf_ms, "archive")
    _require_aligned(processed_rows, tf_ms, "processed")

    archive_map = {row["timestamp"]: row for row in archive_rows}
    processed_map = {row["timestamp"]: row for row in processed_rows}
    common_ts = sorted(set(archive_map) & set(processed_map))
    archive_only = sorted(set(archive_map) - set(processed_map))
    processed_only = sorted(set(processed_map) - set(archive_map))

    field_mismatches = {field: 0 for field in OHLCV_FIELDS}
    examples: list[dict[str, Any]] = []
    matched_bars = 0
    mismatched_bars = 0
    for ts in common_ts:
        left, right = archive_map[ts], processed_map[ts]
        diffs: dict[str, dict[str, str]] = {}
        for field in OHLCV_FIELDS:
            if left[field] != right[field]:
                field_mismatches[field] += 1
                diffs[field] = {
                    "archive": str(left[field]),
                    "processed": str(right[field]),
                }
        if diffs:
            mismatched_bars += 1
            if len(examples) < mismatch_example_limit:
                examples.append({"timestamp": ts, "fields": diffs})
        else:
            matched_bars += 1

    common_n = len(common_ts)
    match_ratio = None if common_n == 0 else matched_bars / common_n
    if common_n == 0:
        warnings.append("no_common_timestamps; match_ratio is null")
    if not archive_rows:
        warnings.append("archive_series_empty")
    if not processed_rows:
        warnings.append("processed_series_empty")
    if archive_only:
        warnings.append("archive_only_timestamps")
    if processed_only:
        warnings.append("processed_only_timestamps")

    if mismatched_bars:
        status = "mismatched"
    else:
        status = "matched"

    return {
        "schema": OVERLAP_SCHEMA,
        "experimental": True,
        "status": status,
        "symbol": wanted,
        "timeframe": timeframe,
        "sources": {
            "archive": archive_path,
            "processed": processed_path_str,
        },
        "comparison_method": COMPARISON_METHOD,
        "mismatch_example_limit": mismatch_example_limit,
        "counts": {
            "archive_bars": len(archive_rows),
            "processed_bars": len(processed_rows),
            "common_timestamps": common_n,
            "archive_only_timestamps": len(archive_only),
            "processed_only_timestamps": len(processed_only),
            "matched_ohlcv_bars": matched_bars,
            "mismatched_ohlcv_bars": mismatched_bars,
            "field_mismatches": field_mismatches,
        },
        "match_ratio": {
            "value": match_ratio,
            "numerator": "matched_ohlcv_bars",
            "denominator": "common_timestamps",
            "null_if_no_common": True,
        },
        "range": {
            "archive_first": archive_rows[0]["timestamp"] if archive_rows else None,
            "archive_last": archive_rows[-1]["timestamp"] if archive_rows else None,
            "processed_first": processed_rows[0]["timestamp"] if processed_rows else None,
            "processed_last": processed_rows[-1]["timestamp"] if processed_rows else None,
            "overlap_first": common_ts[0] if common_ts else None,
            "overlap_last": common_ts[-1] if common_ts else None,
        },
        "duplicates": {"archive": 0, "processed": 0},
        "mismatch_examples": examples,
        "archive_only_sample": archive_only[:mismatch_example_limit],
        "processed_only_sample": processed_only[:mismatch_example_limit],
        "warnings": warnings,
    }


def write_overlap_report(report: Mapping[str, Any], report_path: Path) -> Path:
    _refuse_processed_path(report_path)
    atomic_write_json(report_path, dict(report))
    return report_path


def analyze_archive_processed_files(
    *,
    root: Path,
    symbol: str,
    timeframe: str,
    archive_series_path: Path,
    report_path: Path,
    mismatch_example_limit: int = DEFAULT_EXAMPLE_LIMIT,
) -> dict[str, Any]:
    timeframe_ms(timeframe)
    _refuse_processed_path(report_path)
    processed_file = processed_path(
        Path(root) / "data" / "processed" / "ohlcv", symbol, timeframe
    )
    load_processed(symbol, timeframe, root=root)
    archive_payload = load_decimal_ohlcv_file(archive_series_path)
    processed_payload = load_decimal_ohlcv_file(processed_file)
    report = analyze_series_overlap(
        archive_payload,
        processed_payload,
        symbol=symbol,
        timeframe=timeframe,
        archive_path=archive_series_path.as_posix(),
        processed_path_str=processed_file.as_posix(),
        mismatch_example_limit=mismatch_example_limit,
    )
    write_overlap_report(report, report_path)
    return report


def _decimal_bars(rows: Any, label: str) -> list[dict[str, Any]]:
    if rows is None:
        rows = []
    if not isinstance(rows, list):
        raise ArchiveOverlapError(f"{label} candles must be a list")
    bars: list[dict[str, Any]] = []
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise ArchiveOverlapError(f"{label} candle {index} is not an object")
        try:
            timestamp = int(row["timestamp"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ArchiveOverlapError(
                f"{label} candle {index} has invalid timestamp"
            ) from exc
        bar: dict[str, Any] = {"timestamp": timestamp}
        for field in OHLCV_FIELDS:
            bar[field] = _require_decimal(row.get(field), f"{label} {field} at {timestamp}")
        bars.append(bar)
    return bars


def _require_decimal(value: Any, name: str) -> Decimal:
    if isinstance(value, float):
        raise ArchiveOverlapError(
            f"{name} is a binary float; refuse comparison without JSON Decimal tokens"
        )
    if isinstance(value, bool) or value is None:
        raise ArchiveOverlapError(f"{name} is not a decimal")
    if isinstance(value, Decimal):
        return value
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, str):
        try:
            return Decimal(value)
        except Exception as exc:
            raise ArchiveOverlapError(f"{name} is not a decimal") from exc
    raise ArchiveOverlapError(f"{name} is not a decimal")


def _duplicate_timestamps(rows: Sequence[Mapping[str, Any]]) -> int:
    counts = Counter(int(row["timestamp"]) for row in rows)
    return sum(n - 1 for n in counts.values() if n > 1)


def _require_aligned(rows: Sequence[Mapping[str, Any]], tf_ms: int, label: str) -> None:
    for row in rows:
        ts = int(row["timestamp"])
        if ts % tf_ms != 0:
            raise ArchiveOverlapError(
                f"{label} timestamp {ts} is not aligned to timeframe {tf_ms} ms"
            )


def _failed_report(
    *,
    symbol: str,
    timeframe: str,
    archive_path: str,
    processed_path_str: str,
    archive_rows: Sequence[Mapping[str, Any]],
    processed_rows: Sequence[Mapping[str, Any]],
    archive_dups: int,
    processed_dups: int,
    warnings: list[str],
    reason: str,
) -> dict[str, Any]:
    return {
        "schema": OVERLAP_SCHEMA,
        "experimental": True,
        "status": "failed",
        "symbol": symbol,
        "timeframe": timeframe,
        "sources": {"archive": archive_path, "processed": processed_path_str},
        "comparison_method": COMPARISON_METHOD,
        "counts": {
            "archive_bars": len(archive_rows),
            "processed_bars": len(processed_rows),
            "common_timestamps": 0,
            "archive_only_timestamps": 0,
            "processed_only_timestamps": 0,
            "matched_ohlcv_bars": 0,
            "mismatched_ohlcv_bars": 0,
            "field_mismatches": {field: 0 for field in OHLCV_FIELDS},
        },
        "match_ratio": {
            "value": None,
            "numerator": "matched_ohlcv_bars",
            "denominator": "common_timestamps",
            "null_if_no_common": True,
        },
        "duplicates": {"archive": archive_dups, "processed": processed_dups},
        "mismatch_examples": [],
        "warnings": warnings,
        "failure_reason": reason,
    }


def _refuse_processed_path(path: Path) -> None:
    posix = path.as_posix().replace("\\", "/")
    if "/data/processed/" in f"/{posix}/" or posix.endswith("data/processed"):
        raise ArchiveOverlapError("refusing to write overlap report under data/processed")

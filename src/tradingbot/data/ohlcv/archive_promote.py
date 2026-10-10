"""Guarded promotion of experimental archive OHLCV into processed data.

Default is dry-run. Apply uses a journal so series+manifest are not left mixed.
Does not change ohlcv_dataset_manifest/v1 or the Candle model.
"""

from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from tradingbot.backtest.dataset import load_backtest_series
from tradingbot.data.io import atomic_write_json, load_json, sha256_file
from tradingbot.data.ohlcv.archive_overlap import (
    COMPARISON_METHOD,
    OHLCV_FIELDS,
    OVERLAP_SCHEMA,
    ArchiveOverlapError,
    analyze_series_overlap,
    load_decimal_ohlcv_file,
    _decimal_bars,
    _require_aligned,
)
from tradingbot.data.ohlcv.manifest import MANIFEST_SCHEMA
from tradingbot.data.ohlcv.models import Candle
from tradingbot.data.ohlcv.quality import find_missing_timestamps
from tradingbot.data.ohlcv.reader import load_processed
from tradingbot.data.ohlcv.store import dump_candles, load_candles, processed_path, safe_symbol
from tradingbot.data.ohlcv.timeframes import timeframe_ms

PROMOTE_SCHEMA = "binance_archive_promote_report/v1"
JOURNAL_SCHEMA = "binance_archive_promote_journal/v1"
ReplaceFn = Callable[[Path, Path], None]


class ArchivePromoteError(ValueError):
    """Promotion cannot proceed safely."""


@dataclass(frozen=True)
class ArchivePromoteRequest:
    root: Path
    symbol: str
    timeframe: str
    archive_series_path: Path
    report_path: Path
    apply: bool = False
    accept_json_decimal: bool = False


def replace_file(src: Path, dst: Path) -> None:
    os.replace(src, dst)


def _checkpoint(crash_at: str | None, name: str) -> None:
    if crash_at == name:
        os._exit(91)


def promote_archive_series(
    request: ArchivePromoteRequest,
    *,
    replace_fn: ReplaceFn | None = None,
    crash_at: str | None = None,
    loader_fn: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    """Dry-run by default. Writes processed files only when request.apply is true."""
    replacer = replace_fn or replace_file
    loader = loader_fn or load_backtest_series
    timeframe_ms(request.timeframe)
    _refuse_processed_report(request.report_path)
    root = request.root
    processed_root = root / "data" / "processed" / "ohlcv"
    series_path = processed_path(processed_root, request.symbol, request.timeframe)
    manifest_path = processed_root / f"manifest_{request.timeframe}.json"
    journal_dir = processed_root / ".promote_journal" / f"{safe_symbol(request.symbol)}_{request.timeframe}"

    if request.apply:
        early = _reconcile_journal(journal_dir, series_path, manifest_path)
        if early["kind"] == "complete":
            shutil.rmtree(journal_dir, ignore_errors=True)
            report = {
                "schema": PROMOTE_SCHEMA,
                "experimental": True,
                "mode": "apply",
                "status": "recovered_complete",
                "files_changed": False,
                "symbol": safe_symbol(request.symbol),
                "timeframe": request.timeframe,
                "failure_reason": None,
                "warnings": [],
                "journal_inspection": {
                    "kind": early["kind"],
                    "detail": early["detail"],
                    "failed_checks": early["failed_checks"],
                },
                "after": {
                    "processed_sha256": sha256_file(series_path),
                    "manifest_sha256": sha256_file(manifest_path),
                },
                "manifest_validation": "ok",
                "comparison_method": COMPARISON_METHOD,
            }
            _write_report(request.report_path, report)
            return report
        if early["kind"] != "none":
            report = {
                "schema": PROMOTE_SCHEMA,
                "experimental": True,
                "mode": "apply",
                "status": "rejected",
                "files_changed": early["kind"] in {
                    "series_only",
                    "ambiguous",
                    "backup_invalid",
                },
                "symbol": safe_symbol(request.symbol),
                "timeframe": request.timeframe,
                "failure_reason": f"incomplete_promote_journal:{early['kind']}",
                "warnings": ["promote_journal_present"],
                "journal_inspection": {
                    "kind": early["kind"],
                    "detail": early["detail"],
                    "failed_checks": early["failed_checks"],
                },
                "comparison_method": COMPARISON_METHOD,
            }
            _write_report(request.report_path, report)
            return report

    archive_payload = load_decimal_ohlcv_file(request.archive_series_path)
    processed_payload = load_decimal_ohlcv_file(series_path)
    load_processed(request.symbol, request.timeframe, root=root)
    manifest = load_json(manifest_path)
    _validate_existing_manifest(manifest, request, series_path, root)

    try:
        overlap = analyze_series_overlap(
            archive_payload,
            processed_payload,
            symbol=request.symbol,
            timeframe=request.timeframe,
            archive_path=request.archive_series_path.as_posix(),
            processed_path_str=series_path.as_posix(),
        )
    except ArchiveOverlapError as exc:
        report = {
            "schema": PROMOTE_SCHEMA,
            "experimental": True,
            "mode": "apply" if request.apply else "dry_run",
            "status": "rejected",
            "files_changed": False,
            "failure_reason": str(exc),
            "symbol": safe_symbol(request.symbol),
            "timeframe": request.timeframe,
            "warnings": [],
            "comparison_method": COMPARISON_METHOD,
        }
        _write_report(request.report_path, report)
        return report

    report = _plan_report(
        request=request,
        overlap=overlap,
        archive_payload=archive_payload,
        processed_payload=processed_payload,
        series_path=series_path,
        manifest_path=manifest_path,
    )

    if overlap["status"] == "failed":
        report["status"] = "rejected"
        report["failure_reason"] = overlap.get("failure_reason") or "overlap_failed"
        _write_report(request.report_path, report)
        return report
    if overlap["status"] == "mismatched":
        report["status"] = "rejected"
        report["failure_reason"] = "ohlcv_mismatch_on_common_timestamps"
        _write_report(request.report_path, report)
        return report

    try:
        merged = _merge_bars(archive_payload, processed_payload, request.timeframe)
    except ArchivePromoteError as exc:
        report["status"] = "rejected"
        report["failure_reason"] = str(exc)
        _write_report(request.report_path, report)
        return report

    _fill_merge_stats(report, merged, request.timeframe)
    if not request.apply:
        report["status"] = "dry_run"
        report["files_changed"] = False
        report["apply_blocked_reason"] = None
        if not request.accept_json_decimal:
            report["apply_blocked_reason"] = "json_decimal_not_original_csv"
        _write_report(request.report_path, report)
        return report

    if not request.accept_json_decimal:
        report["status"] = "rejected"
        report["failure_reason"] = "json_decimal_not_original_csv"
        report["files_changed"] = False
        _write_report(request.report_path, report)
        return report

    try:
        _apply_transaction(
            request=request,
            merged=merged,
            manifest=manifest,
            series_path=series_path,
            manifest_path=manifest_path,
            journal_dir=journal_dir,
            replace_fn=replacer,
            crash_at=crash_at,
            loader_fn=loader,
        )
    except Exception as exc:
        report["status"] = "failed"
        report["failure_reason"] = str(exc)
        inspection = _reconcile_journal(journal_dir, series_path, manifest_path)
        report["journal_inspection"] = {
            "kind": inspection["kind"],
            "detail": inspection["detail"],
            "failed_checks": inspection["failed_checks"],
        }
        report["files_changed"] = inspection["kind"] in {
            "series_only",
            "complete",
            "ambiguous",
            "backup_invalid",
        }
        if journal_dir.exists():
            report["warnings"].append("promote_journal_present_after_failure")
        _write_report(request.report_path, report)
        return report

    report["status"] = "applied"
    report["files_changed"] = True
    report["after"]["processed_sha256"] = sha256_file(series_path)
    report["after"]["manifest_sha256"] = sha256_file(manifest_path)
    report["manifest_validation"] = "ok"
    _write_report(request.report_path, report)
    return report


def _plan_report(
    *,
    request: ArchivePromoteRequest,
    overlap: dict[str, Any],
    archive_payload: dict[str, Any],
    processed_payload: dict[str, Any],
    series_path: Path,
    manifest_path: Path,
) -> dict[str, Any]:
    archive_n = len(archive_payload.get("candles") or [])
    processed_n = len(processed_payload.get("candles") or [])
    return {
        "schema": PROMOTE_SCHEMA,
        "experimental": True,
        "mode": "apply" if request.apply else "dry_run",
        "status": "planned",
        "symbol": safe_symbol(request.symbol),
        "timeframe": request.timeframe,
        "files_changed": False,
        "sources": {
            "archive": request.archive_series_path.as_posix(),
            "archive_sha256": sha256_file(request.archive_series_path),
            "processed": series_path.as_posix(),
            "processed_sha256": sha256_file(series_path),
            "manifest": manifest_path.as_posix(),
            "manifest_sha256": sha256_file(manifest_path),
        },
        "overlap_schema": OVERLAP_SCHEMA,
        "overlap_status": overlap["status"],
        "counts": {
            "archive_bars": archive_n,
            "processed_bars_before": processed_n,
            "processed_bars_after": None,
            "added_bars": overlap["counts"]["archive_only_timestamps"],
            "common_matched": overlap["counts"]["matched_ohlcv_bars"],
            "rejected_mismatch_bars": overlap["counts"]["mismatched_ohlcv_bars"],
            "processed_preserved": overlap["counts"]["processed_only_timestamps"]
            + overlap["counts"]["matched_ohlcv_bars"],
        },
        "range": {"first_timestamp": None, "last_timestamp": None},
        "gaps": [],
        "comparison_method": COMPARISON_METHOD,
        "ohlcv_mismatches": overlap["counts"]["field_mismatches"],
        "mismatch_examples": overlap.get("mismatch_examples") or [],
        "manifest_validation": "checked_existing",
        "warnings": list(overlap.get("warnings") or []),
        "after": {},
        "failure_reason": None,
        "apply_blocked_reason": None,
    }


def _merge_bars(
    archive_payload: dict[str, Any],
    processed_payload: dict[str, Any],
    timeframe: str,
) -> list[Candle]:
    tf_ms = timeframe_ms(timeframe)
    archive_rows = _decimal_bars(archive_payload.get("candles"), "archive")
    processed_rows = _decimal_bars(processed_payload.get("candles"), "processed")
    _require_aligned(archive_rows, tf_ms, "archive")
    _require_aligned(processed_rows, tf_ms, "processed")
    archive_map = {int(row["timestamp"]): row for row in archive_rows}
    processed_map = {int(row["timestamp"]): row for row in processed_rows}
    if len(archive_map) != len(archive_rows) or len(processed_map) != len(processed_rows):
        raise ArchivePromoteError("duplicate timestamps")
    merged_rows: list[dict[str, Any]] = []
    for ts in sorted(set(archive_map) | set(processed_map)):
        if ts in archive_map and ts in processed_map:
            left, right = archive_map[ts], processed_map[ts]
            if any(left[field] != right[field] for field in OHLCV_FIELDS):
                raise ArchivePromoteError("ohlcv_mismatch_on_common_timestamps")
            merged_rows.append(processed_map[ts])
        elif ts in processed_map:
            merged_rows.append(processed_map[ts])
        else:
            merged_rows.append(archive_map[ts])
    for ts in processed_map:
        if ts not in {int(row["timestamp"]) for row in merged_rows}:
            raise ArchivePromoteError("merge would drop processed history")
    return [
        Candle(
            timestamp=int(row["timestamp"]),
            open=float(row["open"]),
            high=float(row["high"]),
            low=float(row["low"]),
            close=float(row["close"]),
            volume=float(row["volume"]),
        )
        for row in merged_rows
    ]


def _fill_merge_stats(report: dict[str, Any], merged: list[Candle], timeframe: str) -> None:
    tf_ms = timeframe_ms(timeframe)
    stamps = [item.timestamp for item in merged]
    gaps = find_missing_timestamps(stamps, tf_ms)
    report["counts"]["processed_bars_after"] = len(merged)
    report["range"]["first_timestamp"] = stamps[0] if stamps else None
    report["range"]["last_timestamp"] = stamps[-1] if stamps else None
    report["gaps"] = gaps
    if gaps:
        report["warnings"].append("merged_series_has_gaps_not_filled")


def _validate_existing_manifest(
    manifest: dict[str, Any],
    request: ArchivePromoteRequest,
    series_path: Path,
    root: Path,
) -> str:
    if manifest.get("schema") != MANIFEST_SCHEMA:
        raise ArchivePromoteError("unsupported manifest schema")
    if str(manifest.get("timeframe")) != request.timeframe:
        raise ArchivePromoteError("manifest timeframe mismatch")
    universe = manifest.get("universe")
    if not isinstance(universe, dict):
        raise ArchivePromoteError("manifest universe missing")
    symbols = universe.get("symbols")
    if not isinstance(symbols, list):
        raise ArchivePromoteError("manifest universe.symbols missing")
    manifest_symbol = _manifest_symbol(symbols, request.symbol)
    if manifest_symbol in (manifest.get("incomplete_symbols") or []):
        raise ArchivePromoteError(f"symbol {manifest_symbol!r} is incomplete in manifest")
    universe_rel = str(universe.get("path") or "")
    universe_file = root / universe_rel
    if sha256_file(universe_file) != universe.get("sha256"):
        raise ArchivePromoteError("universe hash mismatch")
    rows = [row for row in manifest.get("series") or [] if row.get("symbol") == manifest_symbol]
    if len(rows) != 1:
        raise ArchivePromoteError("manifest series row missing or duplicated")
    row = rows[0]
    if sha256_file(series_path) != row.get("sha256"):
        raise ArchivePromoteError("processed hash mismatch vs manifest")
    expected = processed_path(
        root / "data" / "processed" / "ohlcv", request.symbol, request.timeframe
    ).resolve()
    if series_path.resolve() != expected:
        raise ArchivePromoteError("processed path does not match reader layout")
    return manifest_symbol


def _manifest_symbol(symbols: list[Any], wanted: str) -> str:
    safe = safe_symbol(wanted)
    matched = [str(item) for item in symbols if safe_symbol(str(item)) == safe]
    if len(matched) != 1:
        raise ArchivePromoteError(f"symbol {wanted!r} not uniquely in universe")
    return matched[0]


def _apply_transaction(
    *,
    request: ArchivePromoteRequest,
    merged: list[Candle],
    manifest: dict[str, Any],
    series_path: Path,
    manifest_path: Path,
    journal_dir: Path,
    replace_fn: ReplaceFn,
    crash_at: str | None = None,
    loader_fn: Callable[..., Any] = load_backtest_series,
) -> None:
    journal_dir.mkdir(parents=True, exist_ok=True)
    backup_series = journal_dir / "backup_series.json"
    backup_manifest = journal_dir / "backup_manifest.json"
    new_series = journal_dir / "new_series.json"
    new_manifest = journal_dir / "new_manifest.json"
    shutil.copyfile(series_path, backup_series)
    shutil.copyfile(manifest_path, backup_manifest)
    pre_series = sha256_file(series_path)
    pre_manifest = sha256_file(manifest_path)
    backup_series_sha = sha256_file(backup_series)
    backup_manifest_sha = sha256_file(backup_manifest)
    if backup_series_sha != pre_series:
        raise ArchivePromoteError("backup_series hash mismatch after copy")
    if backup_manifest_sha != pre_manifest:
        raise ArchivePromoteError("backup_manifest hash mismatch after copy")
    dump_candles(
        new_series,
        merged,
        symbol=str(processed_payload_symbol(manifest, request.symbol)),
        timeframe=request.timeframe,
    )
    reloaded = load_candles(new_series)
    if [c.timestamp for c in reloaded] != [c.timestamp for c in merged]:
        raise ArchivePromoteError("rewritten series timestamps do not match plan")
    if len(reloaded) != len(merged):
        raise ArchivePromoteError("rewritten series length mismatch")
    updated = _updated_manifest(manifest, request, new_series, reloaded)
    atomic_write_json(new_manifest, updated)
    hashes = {
        "pre_series_sha256": pre_series,
        "pre_manifest_sha256": pre_manifest,
        "backup_series_sha256": backup_series_sha,
        "backup_manifest_sha256": backup_manifest_sha,
        "planned_series_sha256": sha256_file(new_series),
        "planned_manifest_sha256": sha256_file(new_manifest),
    }
    _write_journal(
        journal_dir,
        state="backed_up",
        series_path=series_path,
        manifest_path=manifest_path,
        backup_series=backup_series,
        backup_manifest=backup_manifest,
        **hashes,
    )
    _checkpoint(crash_at, "after_backed_up")
    series_published = False
    try:
        replace_fn(new_series, series_path)
        series_published = True
        _checkpoint(crash_at, "after_series_replace")
        _write_journal(
            journal_dir,
            state="series_published",
            series_path=series_path,
            manifest_path=manifest_path,
            backup_series=backup_series,
            backup_manifest=backup_manifest,
            **hashes,
        )
        _checkpoint(crash_at, "after_series_published")
        replace_fn(new_manifest, manifest_path)
        _checkpoint(crash_at, "after_manifest_replace")
        _write_journal(
            journal_dir,
            state="committed",
            series_path=series_path,
            manifest_path=manifest_path,
            backup_series=backup_series,
            backup_manifest=backup_manifest,
            **hashes,
        )
        _checkpoint(crash_at, "after_committed")
        loader_fn(
            Path("data/processed/ohlcv") / f"manifest_{request.timeframe}.json",
            processed_payload_symbol(manifest, request.symbol),
            root=request.root,
        )
    except Exception:
        _rollback(
            journal_dir=journal_dir,
            series_path=series_path,
            manifest_path=manifest_path,
            backup_series=backup_series,
            backup_manifest=backup_manifest,
            series_published=series_published,
            replace_fn=replace_fn,
        )
        raise
    shutil.rmtree(journal_dir, ignore_errors=True)


def processed_payload_symbol(manifest: dict[str, Any], wanted: str) -> str:
    return _manifest_symbol(list(manifest["universe"]["symbols"]), wanted)


def _updated_manifest(
    manifest: dict[str, Any],
    request: ArchivePromoteRequest,
    new_series_path: Path,
    candles: list[Candle],
) -> dict[str, Any]:
    updated = json.loads(json.dumps(manifest))
    symbol = processed_payload_symbol(manifest, request.symbol)
    digest = sha256_file(new_series_path)
    found = False
    for row in updated["series"]:
        if row.get("symbol") == symbol:
            row["sha256"] = digest
            row["range_start"] = candles[0].timestamp
            row["range_end"] = candles[-1].timestamp
            row["candle_count"] = len(candles)
            found = True
            break
    if not found:
        raise ArchivePromoteError("cannot update missing series row")
    incomplete = [
        item for item in (updated.get("incomplete_symbols") or []) if item != symbol
    ]
    updated["incomplete_symbols"] = incomplete
    return updated


def _write_journal(journal_dir: Path, **payload: Any) -> None:
    clean: dict[str, Any] = {"schema": JOURNAL_SCHEMA}
    for key, value in payload.items():
        clean[key] = value.as_posix() if isinstance(value, Path) else value
    atomic_write_json(journal_dir / "journal.json", clean)


def _read_journal(journal_dir: Path) -> dict[str, Any] | None:
    path = journal_dir / "journal.json"
    if not journal_dir.exists():
        return None
    if not path.is_file():
        return {}
    try:
        payload = load_json(path)
    except Exception:
        return {}
    if not isinstance(payload, dict):
        return {}
    return payload


def _hash_if_file(path: Path) -> str | None:
    if not path.is_file():
        return None
    return sha256_file(path)


def _reconcile_journal(
    journal_dir: Path, series_path: Path, manifest_path: Path
) -> dict[str, Any]:
    """Classify leftover journal vs on-disk hashes. Never assumes files are correct."""
    empty = {
        "kind": "none",
        "detail": "no journal",
        "failed_checks": [],
        "current_series_sha256": _hash_if_file(series_path),
        "current_manifest_sha256": _hash_if_file(manifest_path),
    }
    payload = _read_journal(journal_dir)
    if payload is None:
        return empty
    failed: list[str] = []
    if payload.get("schema") != JOURNAL_SCHEMA:
        failed.append("journal_schema")
    backup_series = journal_dir / "backup_series.json"
    backup_manifest = journal_dir / "backup_manifest.json"
    backup_series_sha = _hash_if_file(backup_series)
    backup_manifest_sha = _hash_if_file(backup_manifest)
    expected_backup_series = payload.get("backup_series_sha256")
    expected_backup_manifest = payload.get("backup_manifest_sha256")
    pre_s = payload.get("pre_series_sha256")
    pre_m = payload.get("pre_manifest_sha256")
    plan_s = payload.get("planned_series_sha256")
    plan_m = payload.get("planned_manifest_sha256")
    if backup_series_sha is None:
        failed.append("backup_series_missing")
    elif expected_backup_series and backup_series_sha != expected_backup_series:
        failed.append("backup_series_hash")
    if backup_manifest_sha is None:
        failed.append("backup_manifest_missing")
    elif expected_backup_manifest and backup_manifest_sha != expected_backup_manifest:
        failed.append("backup_manifest_hash")
    current_s = _hash_if_file(series_path)
    current_m = _hash_if_file(manifest_path)
    if not pre_s or not pre_m or not plan_s or not plan_m:
        failed.append("journal_hashes_incomplete")
    result = {
        "kind": "ambiguous",
        "detail": str(payload.get("state") or "unknown"),
        "failed_checks": failed,
        "current_series_sha256": current_s,
        "current_manifest_sha256": current_m,
        "pre_series_sha256": pre_s,
        "pre_manifest_sha256": pre_m,
        "planned_series_sha256": plan_s,
        "planned_manifest_sha256": plan_m,
    }
    if failed:
        result["kind"] = "backup_invalid" if any(
            item.startswith("backup_") for item in failed
        ) else "ambiguous"
        result["detail"] = ",".join(failed)
        return result
    if current_s == pre_s and current_m == pre_m:
        result["kind"] = "stale_unpublished"
        result["detail"] = "journal leftover; live files match pre-promote hashes"
        return result
    if current_s == plan_s and current_m == pre_m:
        result["kind"] = "series_only"
        result["detail"] = "series matches planned hash; manifest still pre-promote"
        return result
    if current_s == plan_s and current_m == plan_m:
        result["kind"] = "complete"
        result["detail"] = "series and manifest match planned hashes"
        return result
    result["kind"] = "ambiguous"
    result["detail"] = (
        f"live hashes match neither pre nor planned "
        f"(series={current_s} manifest={current_m} state={payload.get('state')})"
    )
    return result


def _require_backup(path: Path, expected_sha: str | None, label: str) -> None:
    if not path.is_file():
        raise ArchivePromoteError(f"rollback aborted: {label} missing")
    actual = sha256_file(path)
    if not expected_sha:
        raise ArchivePromoteError(f"rollback aborted: {label} expected hash missing")
    if actual != expected_sha:
        raise ArchivePromoteError(
            f"rollback aborted: {label} hash mismatch expected={expected_sha} actual={actual}"
        )


def _rollback(
    *,
    journal_dir: Path,
    series_path: Path,
    manifest_path: Path,
    backup_series: Path,
    backup_manifest: Path,
    series_published: bool,
    replace_fn: ReplaceFn,
) -> None:
    payload = _read_journal(journal_dir) or {}
    try:
        expected_series = str(payload.get("backup_series_sha256") or payload.get("pre_series_sha256") or "")
        expected_manifest = str(
            payload.get("backup_manifest_sha256") or payload.get("pre_manifest_sha256") or ""
        )
        if series_published:
            _require_backup(backup_series, expected_series or None, "backup_series")
            replace_fn(backup_series, series_path)
        if _hash_if_file(manifest_path) != expected_manifest:
            _require_backup(backup_manifest, expected_manifest or None, "backup_manifest")
            replace_fn(backup_manifest, manifest_path)
    except ArchivePromoteError:
        raise
    except Exception as exc:
        raise ArchivePromoteError(
            f"rollback failed; promote journal remains: {exc}"
        ) from exc
    shutil.rmtree(journal_dir, ignore_errors=True)


def _write_report(path: Path, report: dict[str, Any]) -> None:
    _refuse_processed_report(path)
    atomic_write_json(path, report)


def _refuse_processed_report(path: Path) -> None:
    posix = path.as_posix().replace("\\", "/")
    if "/data/processed/" in f"/{posix}/" or posix.endswith("data/processed"):
        raise ArchivePromoteError("refusing to write promote report under data/processed")

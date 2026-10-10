"""Experimental Binance archive provenance records.

Records ZIP SHA-256, checksum text, CSV member name, and period bounds.
candles_sha256 is the SHA-256 of that generation's candles.json file bytes
(float JSON from dump_candles). It does not hash original CSV cell tokens.
Does not change Candle, processed OHLCV, or promotion.

Live experimental output is a generation directory selected by current.json.
Files inside one generation are still written sequentially; only the current.json
pointer replace is the intended live-set switch.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

from tradingbot.data.io import atomic_write_json, load_json, sha256_file
from tradingbot.data.ohlcv.archive_zip import parse_checksum_file
from tradingbot.data.ohlcv.store import safe_symbol

PROVENANCE_SCHEMA = "binance_archive_provenance/v2"
PROVENANCE_FILENAME = "provenance.json"
_SHA256_HEX = re.compile(r"^[0-9a-f]{64}$")


class ArchiveProvenanceError(ValueError):
    """Provenance record is missing, corrupt, or inconsistent."""


def provenance_path(series_dir: Path) -> Path:
    return series_dir / PROVENANCE_FILENAME


def archive_record(
    *,
    zip_name: str,
    zip_sha256: str,
    checksum_name: str,
    checksum_text: str,
    checksum_sha256: str,
    checksum_verified: bool,
    csv_name: str,
    period_start_ms: int,
    period_end_exclusive_ms: int,
    symbol: str,
    timeframe: str,
    cadence: str,
    zip_url: str,
    checksum_url: str,
) -> dict[str, Any]:
    return {
        "zip_name": zip_name,
        "zip_sha256": zip_sha256,
        "checksum_name": checksum_name,
        "checksum_text": checksum_text,
        "checksum_sha256": checksum_sha256,
        "checksum_verified": checksum_verified,
        "csv_name": csv_name,
        "period_start_ms": period_start_ms,
        "period_end_exclusive_ms": period_end_exclusive_ms,
        "symbol": safe_symbol(symbol),
        "timeframe": timeframe,
        "cadence": cadence,
        "zip_url": zip_url,
        "checksum_url": checksum_url,
    }


def build_provenance_payload(
    *,
    symbol: str,
    timeframe: str,
    cadence: str,
    range_start_ms: int,
    range_end_exclusive_ms: int,
    archives: list[dict[str, Any]],
    candles_sha256: str | None = None,
) -> dict[str, Any]:
    ordered = sorted(archives, key=lambda item: str(item.get("zip_name") or ""))
    payload = {
        "schema": PROVENANCE_SCHEMA,
        "experimental": True,
        "not_ohlcv_dataset_manifest": True,
        "symbol": safe_symbol(symbol),
        "timeframe": timeframe,
        "cadence": cadence,
        "range_start_ms": range_start_ms,
        "range_end_exclusive_ms": range_end_exclusive_ms,
        "archives": ordered,
    }
    if candles_sha256 is not None:
        payload["candles_sha256"] = candles_sha256
    return payload


def write_provenance(path: Path, payload: dict[str, Any]) -> str:
    """Write canonical JSON and return SHA-256 of the resulting file bytes."""
    validate_provenance_payload(payload)
    if path.name != PROVENANCE_FILENAME:
        raise ArchiveProvenanceError(
            f"provenance filename must be {PROVENANCE_FILENAME!r}, got {path.name!r}"
        )
    atomic_write_json(path, payload)
    return sha256_file(path)


def provenance_ref(sha256: str) -> dict[str, str]:
    digest = _require_sha256(sha256, "provenance")
    return {
        "path": PROVENANCE_FILENAME,
        "schema": PROVENANCE_SCHEMA,
        "sha256": digest,
    }


def validate_provenance_payload(payload: Any) -> None:
    if not isinstance(payload, dict):
        raise ArchiveProvenanceError("provenance payload must be an object")
    if payload.get("schema") != PROVENANCE_SCHEMA:
        raise ArchiveProvenanceError("unsupported provenance schema")
    if payload.get("experimental") is not True:
        raise ArchiveProvenanceError("provenance must be experimental")
    for key in ("symbol", "timeframe", "cadence"):
        if not isinstance(payload.get(key), str) or not payload[key]:
            raise ArchiveProvenanceError(f"provenance {key} missing")
    if payload["cadence"] not in {"monthly", "daily"}:
        raise ArchiveProvenanceError("provenance cadence must be monthly or daily")
    start = payload.get("range_start_ms")
    end = payload.get("range_end_exclusive_ms")
    if not isinstance(start, int) or not isinstance(end, int) or end <= start:
        raise ArchiveProvenanceError("provenance range must be non-empty [start, end)")
    archives = payload.get("archives")
    if not isinstance(archives, list):
        raise ArchiveProvenanceError("provenance archives must be a list")
    names: list[str] = []
    for index, item in enumerate(archives):
        _validate_archive_record(item, index=index)
        names.append(str(item["zip_name"]))
    if names != sorted(names):
        raise ArchiveProvenanceError("provenance archives must be sorted by zip_name")
    if len(names) != len(set(names)):
        raise ArchiveProvenanceError("provenance archives contain duplicate zip_name")
    _require_sha256(payload.get("candles_sha256"), "candles")


def validate_provenance_file(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise ArchiveProvenanceError(f"missing provenance file: {path}")
    try:
        payload = load_json(path)
    except Exception as exc:
        raise ArchiveProvenanceError(f"invalid provenance json: {path}") from exc
    validate_provenance_payload(payload)
    return payload


def validate_report_provenance_link(
    report: dict[str, Any],
    series_dir: Path,
    *,
    provenance_sha256: str | None = None,
) -> None:
    """Require import report to point at a matching provenance file in series_dir."""
    pointer = report.get("provenance")
    if not isinstance(pointer, dict):
        raise ArchiveProvenanceError("import report missing provenance reference")
    if pointer.get("path") != PROVENANCE_FILENAME:
        raise ArchiveProvenanceError("import report provenance path mismatch")
    if pointer.get("schema") != PROVENANCE_SCHEMA:
        raise ArchiveProvenanceError("import report provenance schema mismatch")
    expected = _require_sha256(pointer.get("sha256"), "import report provenance")
    path = provenance_path(series_dir)
    if not path.is_file():
        raise ArchiveProvenanceError("provenance file missing for import report")
    actual = sha256_file(path)
    if actual != expected:
        raise ArchiveProvenanceError(
            f"provenance hash mismatch expected={expected} actual={actual}"
        )
    if provenance_sha256 is not None and provenance_sha256 != actual:
        raise ArchiveProvenanceError("provenance hash does not match caller digest")
    payload = validate_provenance_file(path)
    for key in ("symbol", "timeframe", "cadence", "range_start_ms", "range_end_exclusive_ms"):
        if report.get(key) != payload.get(key):
            raise ArchiveProvenanceError(f"import report {key} does not match provenance")
    ok_periods = [
        item for item in (report.get("periods") or []) if isinstance(item, dict) and item.get("status") == "ok"
    ]
    archives = {item["zip_name"]: item for item in payload["archives"]}
    if len(ok_periods) != len(archives):
        raise ArchiveProvenanceError("import report ok periods do not match provenance archives")
    for period in ok_periods:
        name = period.get("zip_name")
        record = archives.get(name)
        if record is None:
            raise ArchiveProvenanceError(f"provenance missing archive {name!r}")
        if period.get("sha256") != record["zip_sha256"]:
            raise ArchiveProvenanceError(f"zip sha256 mismatch for {name}")
        if period.get("csv_name") != record["csv_name"]:
            raise ArchiveProvenanceError(f"csv member mismatch for {name}")
        if period.get("period_start_ms") != record["period_start_ms"]:
            raise ArchiveProvenanceError(f"period start mismatch for {name}")
        if period.get("period_end_exclusive_ms") != record["period_end_exclusive_ms"]:
            raise ArchiveProvenanceError(f"period end mismatch for {name}")


def checksum_body_sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _validate_archive_record(item: Any, *, index: int) -> None:
    if not isinstance(item, dict):
        raise ArchiveProvenanceError(f"archive[{index}] must be an object")
    zip_name = item.get("zip_name")
    csv_name = item.get("csv_name")
    if not isinstance(zip_name, str) or not zip_name.endswith(".zip"):
        raise ArchiveProvenanceError(f"archive[{index}] zip_name invalid")
    if csv_name != zip_name[:-4] + ".csv":
        raise ArchiveProvenanceError(f"archive[{index}] csv_name does not match zip_name")
    if "/" in zip_name or "\\" in zip_name or ".." in zip_name:
        raise ArchiveProvenanceError(f"archive[{index}] unsafe zip_name")
    _require_sha256(item.get("zip_sha256"), f"archive[{index}] zip")
    checksum_name = item.get("checksum_name")
    if checksum_name != f"{zip_name}.CHECKSUM":
        raise ArchiveProvenanceError(f"archive[{index}] checksum_name mismatch")
    text = item.get("checksum_text")
    if not isinstance(text, str) or not text:
        raise ArchiveProvenanceError(f"archive[{index}] checksum_text missing")
    digest = parse_checksum_file(text, expected_zip_name=zip_name)
    if digest != item.get("zip_sha256"):
        raise ArchiveProvenanceError(f"archive[{index}] checksum does not match zip_sha256")
    body_sha = checksum_body_sha256(text)
    if item.get("checksum_sha256") != body_sha:
        raise ArchiveProvenanceError(f"archive[{index}] checksum_sha256 mismatch")
    if item.get("checksum_verified") is not True:
        raise ArchiveProvenanceError(f"archive[{index}] checksum was not verified")
    start = item.get("period_start_ms")
    end = item.get("period_end_exclusive_ms")
    if not isinstance(start, int) or not isinstance(end, int) or end <= start:
        raise ArchiveProvenanceError(f"archive[{index}] period must be [start, end)")
    if item.get("cadence") not in {"monthly", "daily"}:
        raise ArchiveProvenanceError(f"archive[{index}] cadence invalid")


def _require_sha256(value: Any, label: str) -> str:
    if not isinstance(value, str) or not _SHA256_HEX.fullmatch(value):
        raise ArchiveProvenanceError(f"{label} sha256 missing or invalid")
    return value

"""Experimental Binance Spot archive import. Not sync_ohlcv or processed OHLCV."""

from __future__ import annotations

import csv
import io
import json
import os
import re
import shutil
import time
import urllib.error
import urllib.request
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

from tradingbot.data.io import atomic_write_json, load_json, sha256_file
from tradingbot.data.ohlcv.archive_csv import (
    ArchiveKlineParseError,
    month_period_ms,
    parse_archive_kline_rows,
)
from tradingbot.data.ohlcv.archive_provenance import (
    PROVENANCE_FILENAME,
    ArchiveProvenanceError,
    archive_record,
    build_provenance_payload,
    provenance_ref,
    validate_provenance_payload,
    validate_report_provenance_link,
    write_provenance,
)
from tradingbot.data.ohlcv.archive_zip import (
    ARCHIVE_BASE,
    ArchiveHttpClient,
    ArchiveHttpResponse,
    ArchiveZipError,
    KlineArchiveRef,
    daily_kline_archive_ref,
    fetch_verified_kline_archive,
    monthly_kline_archive_ref,
    parse_checksum_file,
)
from tradingbot.data.ohlcv.models import Candle
from tradingbot.data.ohlcv.quality import filter_candles
from tradingbot.data.ohlcv.store import dump_candles, safe_symbol
from tradingbot.data.ohlcv.timeframes import UnsupportedTimeframeError, timeframe_ms

REPORT_SCHEMA = "binance_archive_import_report/v1"
CURRENT_SCHEMA = "binance_archive_current/v1"
CURRENT_FILENAME = "current.json"
EXPERIMENTAL_REL = Path("data") / "experimental" / "binance_archive"
_GENERATION_RE = re.compile(r"^v-[0-9a-f]{32}$")
ReplaceFn = Callable[[Path, Path], None]


class ArchiveImportError(ValueError):
    """Experimental archive import cannot produce a safe series."""


@dataclass(frozen=True)
class ArchiveImportRequest:
    symbol: str
    timeframe: str
    start_ms: int
    end_exclusive_ms: int
    cadence: str
    dest_root: Path
    now_ms: int | None = None


class UrllibArchiveHttpClient:
    """Public data.binance.vision GET client. Instantiation does not fetch."""

    def fetch(self, url: str) -> ArchiveHttpResponse:
        if not url.startswith(ARCHIVE_BASE + "/"):
            raise ArchiveImportError(f"refusing non-archive url {url!r}")
        req = urllib.request.Request(
            url,
            method="GET",
            headers={"User-Agent": "TradingBot-archive-import/9L.7"},
        )
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                body = resp.read()
                headers = {str(k): str(v) for k, v in resp.headers.items()}
                return ArchiveHttpResponse(status=resp.status, body=body, headers=headers)
        except urllib.error.HTTPError as exc:
            body = exc.read() if exc.fp is not None else b""
            headers = {str(k): str(v) for k, v in (exc.headers.items() if exc.headers else [])}
            return ArchiveHttpResponse(status=exc.code, body=body, headers=headers)


def experimental_series_dir(dest_root: Path, symbol: str, timeframe: str) -> Path:
    return dest_root / safe_symbol(symbol) / timeframe


def _checkpoint(crash_at: str | None, name: str) -> None:
    if crash_at == name:
        os._exit(91)


def current_pointer_path(series_dir: Path) -> Path:
    return series_dir / CURRENT_FILENAME


def resolve_current_generation(series_dir: Path) -> Path:
    """Return the live generation directory. Fail-closed; never reads legacy root files."""
    pointer = current_pointer_path(series_dir)
    if not pointer.is_file():
        raise ArchiveImportError("experimental current.json missing")
    try:
        payload = load_json(pointer)
    except Exception as exc:
        raise ArchiveImportError("experimental current.json is invalid") from exc
    if not isinstance(payload, dict) or payload.get("schema") != CURRENT_SCHEMA:
        raise ArchiveImportError("unsupported experimental current schema")
    if payload.get("experimental") is not True:
        raise ArchiveImportError("experimental current.json must be experimental")
    generation = payload.get("generation")
    path_token = payload.get("path")
    if generation != path_token or not isinstance(generation, str):
        raise ArchiveImportError("experimental current.json generation path mismatch")
    if not _GENERATION_RE.fullmatch(generation):
        raise ArchiveImportError("experimental current.json generation id invalid")
    generation_dir = (series_dir / generation).resolve()
    try:
        generation_dir.relative_to(series_dir.resolve())
    except ValueError as exc:
        raise ArchiveImportError("experimental generation escapes series dir") from exc
    if not generation_dir.is_dir():
        raise ArchiveImportError(f"experimental generation missing: {generation}")
    validate_generation_dir(generation_dir)
    return generation_dir


def resolve_archive_series_input(path: Path) -> Path:
    """Resolve --archive-series to a candles.json file.

    Directories use the live generation resolver. A candles.json file inside a
    validated generation is accepted after integrity checks. Leftover series-root
    candles.json (beside current.json or generation dirs) is rejected. A
    standalone candles.json with no generation/pointer siblings is returned as-is.
    """
    if path.is_file():
        return _resolve_archive_series_file(path)
    if not path.is_dir():
        raise ArchiveImportError(f"archive series path missing: {path}")
    if current_pointer_path(path).is_file():
        return resolve_current_generation(path) / "candles.json"
    if (path / "import_report.json").is_file() and (path / PROVENANCE_FILENAME).is_file():
        validate_generation_dir(path)
        return path / "candles.json"
    if (path / "candles.json").is_file():
        raise ArchiveImportError(
            "legacy root candles.json is not live; pass a generation directory, "
            "a series directory with current.json, or an explicit candles.json file"
        )
    raise ArchiveImportError("unable to resolve experimental archive series path")


def _resolve_archive_series_file(path: Path) -> Path:
    parent = path.parent
    if path.name == "candles.json" and (parent / "import_report.json").is_file() and (
        parent / PROVENANCE_FILENAME
    ).is_file():
        validate_generation_dir(parent)
        return path
    if path.name == "candles.json" and current_pointer_path(parent).is_file():
        raise ArchiveImportError(
            "legacy root candles.json is not live; pass a generation directory, "
            "a series directory with current.json, or the live generation candles.json"
        )
    if path.name == "candles.json" and any(
        child.is_dir() and _GENERATION_RE.fullmatch(child.name) for child in parent.iterdir()
    ):
        raise ArchiveImportError(
            "legacy root candles.json is not live; pass a generation directory or "
            "a series directory with current.json"
        )
    return path


def validate_generation_dir(generation_dir: Path) -> None:
    candles = generation_dir / "candles.json"
    report_path = generation_dir / "import_report.json"
    if not candles.is_file():
        raise ArchiveImportError("generation candles.json missing")
    if not report_path.is_file():
        raise ArchiveImportError("generation import_report.json missing")
    try:
        report = load_json(report_path)
    except Exception as exc:
        raise ArchiveImportError("generation import_report.json invalid") from exc
    if not isinstance(report, dict):
        raise ArchiveImportError("generation import_report.json invalid")
    try:
        validate_report_provenance_link(report, generation_dir)
    except ArchiveProvenanceError as exc:
        raise ArchiveImportError(f"generation provenance inconsistent: {exc}") from exc
    try:
        provenance = load_json(generation_dir / PROVENANCE_FILENAME)
    except Exception as exc:
        raise ArchiveImportError("generation provenance.json invalid") from exc
    if not isinstance(provenance, dict):
        raise ArchiveImportError("generation provenance.json invalid")
    expected_candles = provenance.get("candles_sha256")
    actual_candles = sha256_file(candles)
    if expected_candles != actual_candles:
        raise ArchiveImportError(
            f"generation candles hash mismatch expected={expected_candles} actual={actual_candles}"
        )
    checksum_dir = generation_dir / "checksums"
    for record in provenance.get("archives") or []:
        name = str(record.get("checksum_name") or "")
        checksum_path = checksum_dir / name
        if not name or not checksum_path.is_file():
            raise ArchiveImportError(f"generation checksum missing: {name}")
        try:
            text = checksum_path.read_text(encoding="utf-8")
        except OSError as exc:
            raise ArchiveImportError(f"generation checksum unreadable: {name}") from exc
        try:
            parsed = parse_checksum_file(text, expected_zip_name=str(record.get("zip_name") or ""))
        except Exception as exc:
            raise ArchiveImportError(f"generation checksum invalid: {name}") from exc
        if parsed != record.get("zip_sha256"):
            raise ArchiveImportError(f"generation checksum zip sha256 mismatch: {name}")
        actual_sum = sha256_file(checksum_path)
        if actual_sum != record.get("checksum_sha256"):
            raise ArchiveImportError(f"generation checksum file hash mismatch: {name}")


def select_archive_refs(
    symbol: str,
    timeframe: str,
    start_ms: int,
    end_exclusive_ms: int,
    cadence: str,
) -> list[KlineArchiveRef]:
    if end_exclusive_ms <= start_ms:
        raise ArchiveImportError("date range must be non-empty [start, end)")
    try:
        if cadence == "monthly":
            return _monthly_refs(symbol, timeframe, start_ms, end_exclusive_ms)
        if cadence == "daily":
            return _daily_refs(symbol, timeframe, start_ms, end_exclusive_ms)
    except (ArchiveZipError, UnsupportedTimeframeError) as exc:
        raise ArchiveImportError(str(exc)) from exc
    raise ArchiveImportError(f"unsupported cadence {cadence!r}")


def import_archive_klines(
    request: ArchiveImportRequest,
    *,
    client: ArchiveHttpClient,
    replace_fn: ReplaceFn | None = None,
    crash_at: str | None = None,
    generation_id: str | None = None,
) -> dict[str, Any]:
    try:
        timeframe_ms(request.timeframe)
    except UnsupportedTimeframeError as exc:
        raise ArchiveImportError(str(exc)) from exc
    if request.cadence not in {"monthly", "daily"}:
        raise ArchiveImportError(f"unsupported cadence {request.cadence!r}")
    _refuse_processed_dest(request.dest_root)
    refs = select_archive_refs(
        request.symbol,
        request.timeframe,
        request.start_ms,
        request.end_exclusive_ms,
        request.cadence,
    )
    series_dir = experimental_series_dir(
        request.dest_root, request.symbol, request.timeframe
    )
    staging = series_dir.parent / f".{request.timeframe}.staging-{uuid4().hex}"
    zip_dir = staging / "zips"
    now_ms = request.now_ms if request.now_ms is not None else int(time.time() * 1000)

    periods: list[dict[str, Any]] = []
    archive_records: list[dict[str, Any]] = []
    parsed_groups: list[list[Candle]] = []
    fetched_rows = 0
    try:
        for ref in refs:
            try:
                verified = fetch_verified_kline_archive(ref, zip_dir, client=client)
                csv_bytes = verified.read_csv_bytes()
                rows = _csv_rows(csv_bytes)
                fetched_rows += len(rows)
                period_start, period_end = _ref_period_ms(ref)
                candles = parse_archive_kline_rows(
                    rows,
                    timeframe=request.timeframe,
                    period_start_ms=period_start,
                    period_end_exclusive_ms=period_end,
                )
            except (ArchiveZipError, ArchiveKlineParseError, UnicodeDecodeError) as exc:
                if isinstance(exc, ArchiveZipError) and "http " in str(exc):
                    periods.append(
                        {
                            "zip_name": ref.zip_name,
                            "url": ref.zip_url,
                            "status": "missing",
                            "message": str(exc),
                        }
                    )
                    continue
                raise ArchiveImportError(
                    f"archive period {ref.zip_name} failed: {exc}"
                ) from exc
            parsed_groups.append(candles)
            record = archive_record(
                zip_name=verified.ref.zip_name,
                zip_sha256=verified.sha256,
                checksum_name=verified.ref.checksum_name,
                checksum_text=verified.checksum_text,
                checksum_sha256=verified.checksum_sha256,
                checksum_verified=verified.checksum_verified,
                csv_name=verified.csv_name,
                period_start_ms=period_start,
                period_end_exclusive_ms=period_end,
                symbol=request.symbol,
                timeframe=request.timeframe,
                cadence=verified.ref.cadence,
                zip_url=verified.ref.zip_url,
                checksum_url=verified.ref.checksum_url,
            )
            archive_records.append(record)
            periods.append(
                {
                    "zip_name": ref.zip_name,
                    "csv_name": verified.csv_name,
                    "sha256": verified.sha256,
                    "url": ref.zip_url,
                    "status": "ok",
                    "parsed": len(candles),
                    "period_start_ms": period_start,
                    "period_end_exclusive_ms": period_end,
                    "checksum_verified": True,
                }
            )

        combined = _combine_parsed_groups(parsed_groups)
        in_range, out_of_range = _split_range(
            combined, request.start_ms, request.end_exclusive_ms
        )
        in_range.sort(key=lambda item: item.timestamp)
        quality_candles, quality_issues = filter_candles(
            in_range,
            request.timeframe,
            now_ms=now_ms,
            drop_incomplete=True,
        )
        missing_periods = [p for p in periods if p["status"] == "missing"]
        if not quality_candles and missing_periods:
            status = "failed"
        elif missing_periods:
            status = "completed_with_missing"
        else:
            status = "success"

        provenance = build_provenance_payload(
            symbol=request.symbol,
            timeframe=request.timeframe,
            cadence=request.cadence,
            range_start_ms=request.start_ms,
            range_end_exclusive_ms=request.end_exclusive_ms,
            archives=archive_records,
        )
        report = _build_report(
            request=request,
            status=status,
            periods=periods,
            fetched_rows=fetched_rows,
            parsed=len(combined),
            in_range=len(in_range),
            out_of_range=out_of_range,
            quality_issues=quality_issues,
            series=quality_candles,
        )
        if status == "failed":
            raise ArchiveImportError("no in-range candles; one or more archive periods missing")
        _publish(
            series_dir,
            staging,
            request,
            quality_candles,
            report,
            provenance,
            replace_fn=replace_fn,
            crash_at=crash_at,
            generation_id=generation_id,
        )
        live = resolve_current_generation(series_dir)
        report["generation"] = live.name
        report["current_path"] = current_pointer_path(series_dir).as_posix()
        report["candles_path"] = (live / "candles.json").as_posix()
        return report
    except Exception:
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
        raise
    finally:
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)


def _monthly_refs(
    symbol: str, timeframe: str, start_ms: int, end_exclusive_ms: int
) -> list[KlineArchiveRef]:
    first = datetime.fromtimestamp(start_ms / 1000, UTC)
    last = datetime.fromtimestamp((end_exclusive_ms - 1) / 1000, UTC)
    cursor = datetime(first.year, first.month, 1, tzinfo=UTC)
    end_month = datetime(last.year, last.month, 1, tzinfo=UTC)
    refs: list[KlineArchiveRef] = []
    while cursor <= end_month:
        refs.append(
            monthly_kline_archive_ref(symbol, timeframe, cursor.year, cursor.month)
        )
        if cursor.month == 12:
            cursor = datetime(cursor.year + 1, 1, 1, tzinfo=UTC)
        else:
            cursor = datetime(cursor.year, cursor.month + 1, 1, tzinfo=UTC)
    return refs


def _daily_refs(
    symbol: str, timeframe: str, start_ms: int, end_exclusive_ms: int
) -> list[KlineArchiveRef]:
    first = datetime.fromtimestamp(start_ms / 1000, UTC).date()
    last = datetime.fromtimestamp((end_exclusive_ms - 1) / 1000, UTC).date()
    refs: list[KlineArchiveRef] = []
    cursor = first
    while cursor <= last:
        refs.append(
            daily_kline_archive_ref(
                symbol, timeframe, cursor.year, cursor.month, cursor.day
            )
        )
        cursor = cursor + timedelta(days=1)
    return refs


def _ref_period_ms(ref: KlineArchiveRef) -> tuple[int, int]:
    if ref.day is None:
        return month_period_ms(ref.year, ref.month)
    start = datetime(ref.year, ref.month, ref.day, tzinfo=UTC)
    end = start + timedelta(days=1)
    return int(start.timestamp()) * 1000, int(end.timestamp()) * 1000


def _csv_rows(csv_bytes: bytes) -> list[list[str]]:
    text = csv_bytes.decode("utf-8")
    return [row for row in csv.reader(io.StringIO(text)) if row]


def _combine_parsed_groups(groups: list[list[Candle]]) -> list[Candle]:
    combined: list[Candle] = []
    for group in groups:
        combined.extend(group)
    counts = Counter(item.timestamp for item in combined)
    duplicates = sorted(ts for ts, n in counts.items() if n > 1)
    if duplicates:
        raise ArchiveImportError(
            f"duplicate timestamps across archive rows: {duplicates[:5]}"
        )
    combined.sort(key=lambda item: item.timestamp)
    return combined


def _split_range(
    candles: list[Candle], start_ms: int, end_exclusive_ms: int
) -> tuple[list[Candle], int]:
    inside: list[Candle] = []
    outside = 0
    for candle in candles:
        if start_ms <= candle.timestamp < end_exclusive_ms:
            inside.append(candle)
        else:
            outside += 1
    return inside, outside


def _build_report(
    *,
    request: ArchiveImportRequest,
    status: str,
    periods: list[dict[str, Any]],
    fetched_rows: int,
    parsed: int,
    in_range: int,
    out_of_range: int,
    quality_issues: list[dict[str, Any]],
    series: list[Candle],
) -> dict[str, Any]:
    return {
        "schema": REPORT_SCHEMA,
        "experimental": True,
        "not_ohlcv_dataset_manifest": True,
        "status": status,
        "symbol": safe_symbol(request.symbol),
        "timeframe": request.timeframe,
        "cadence": request.cadence,
        "range_start_ms": request.start_ms,
        "range_end_exclusive_ms": request.end_exclusive_ms,
        "periods": periods,
        "counts": {
            "fetched_rows": fetched_rows,
            "parsed": parsed,
            "in_range": in_range,
            "out_of_range": out_of_range,
            "duplicate_timestamps": sum(
                1 for item in quality_issues if item.get("reason") == "duplicate_timestamp"
            ),
            "quality_issues": len(quality_issues),
        },
        "quality_issues": quality_issues,
        "series": {
            "candle_count": len(series),
            "first_timestamp": series[0].timestamp if series else None,
            "last_timestamp": series[-1].timestamp if series else None,
        },
    }


def _publish(
    series_dir: Path,
    staging: Path,
    request: ArchiveImportRequest,
    candles: list[Candle],
    report: dict[str, Any],
    provenance: dict[str, Any],
    *,
    replace_fn: ReplaceFn | None = None,
    crash_at: str | None = None,
    generation_id: str | None = None,
) -> None:
    """Publish an immutable generation, then flip current.json with one os.replace."""
    replacer = replace_fn or os.replace
    token = generation_id or f"v-{uuid4().hex}"
    if not _GENERATION_RE.fullmatch(token):
        raise ArchiveImportError("generation id invalid")
    staging.mkdir(parents=True, exist_ok=True)
    candles_tmp = staging / "candles.json"
    report_tmp = staging / "import_report.json"
    provenance_tmp = staging / PROVENANCE_FILENAME
    dump_candles(
        candles_tmp,
        candles,
        symbol=request.symbol,
        timeframe=request.timeframe,
        extra={
            "experimental": True,
            "source": "binance_spot_archive",
            "schema": REPORT_SCHEMA,
        },
    )
    provenance = dict(provenance)
    provenance["candles_sha256"] = sha256_file(candles_tmp)
    try:
        validate_provenance_payload(provenance)
        digest = write_provenance(provenance_tmp, provenance)
        report["provenance"] = provenance_ref(digest)
        report["generation"] = token
        atomic_write_json(report_tmp, report)
        validate_report_provenance_link(report, staging, provenance_sha256=digest)
    except ArchiveProvenanceError as exc:
        raise ArchiveImportError(f"provenance validation failed: {exc}") from exc
    series_dir.mkdir(parents=True, exist_ok=True)
    _checkpoint(crash_at, "before_generation")
    generation_dir = series_dir / token
    generation_dir.mkdir(parents=False, exist_ok=False)
    checksum_dir = generation_dir / "checksums"
    checksum_dir.mkdir(parents=True, exist_ok=True)
    zip_dir = staging / "zips"
    try:
        for record in provenance["archives"]:
            checksum_name = str(record["checksum_name"])
            src = zip_dir / checksum_name
            if not src.is_file():
                raise ArchiveImportError(f"verified checksum missing in staging: {checksum_name}")
            replacer(src, checksum_dir / checksum_name)
        _checkpoint(crash_at, "after_checksum")
        replacer(provenance_tmp, generation_dir / PROVENANCE_FILENAME)
        _checkpoint(crash_at, "after_provenance")
        replacer(candles_tmp, generation_dir / "candles.json")
        _checkpoint(crash_at, "after_candles")
        replacer(report_tmp, generation_dir / "import_report.json")
        _checkpoint(crash_at, "after_report")
    except OSError as exc:
        raise ArchiveImportError(
            f"generation publish failed; current pointer unchanged: {exc}"
        ) from exc
    try:
        validate_generation_dir(generation_dir)
    except ArchiveImportError:
        raise
    _checkpoint(crash_at, "after_generation_validated")
    pointer = {
        "schema": CURRENT_SCHEMA,
        "experimental": True,
        "generation": token,
        "path": token,
    }
    pointer_tmp = series_dir / (CURRENT_FILENAME + ".tmp")
    pointer_tmp.write_text(
        json.dumps(pointer, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    try:
        replacer(pointer_tmp, current_pointer_path(series_dir))
    except OSError as exc:
        if pointer_tmp.exists():
            pointer_tmp.unlink(missing_ok=True)
        raise ArchiveImportError(
            f"current pointer publish failed; generation is not live: {exc}"
        ) from exc
    _checkpoint(crash_at, "after_current")
    resolve_current_generation(series_dir)


def _refuse_processed_dest(dest_root: Path) -> None:
    posix = dest_root.as_posix().replace("\\", "/")
    if "/data/processed/" in f"/{posix}/" or posix.endswith("data/processed"):
        raise ArchiveImportError("refusing to write experimental archive under data/processed")

"""Verify and place Binance Spot archive kline ZIP files.

No Candle parsing, processed store, manifest, or sync_ohlcv integration.
HTTP is injected; this module does not require API keys.
"""

from __future__ import annotations

import hashlib
import io
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol
from zipfile import BadZipFile, ZipFile

from tradingbot.data.ohlcv.timeframes import timeframe_ms

ARCHIVE_BASE = "https://data.binance.vision/data/spot"
_SYMBOL_RE = re.compile(r"^[A-Z0-9]+$")
_SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")


class ArchiveZipError(ValueError):
    """Archive ZIP, checksum, or HTTP verification failed."""


@dataclass(frozen=True)
class ArchiveHttpResponse:
    status: int
    body: bytes
    headers: Mapping[str, str]


class ArchiveHttpClient(Protocol):
    def fetch(self, url: str) -> ArchiveHttpResponse:
        """GET a public archive URL. Must not send API keys."""


@dataclass(frozen=True)
class KlineArchiveRef:
    symbol: str
    timeframe: str
    year: int
    month: int
    day: int | None

    @property
    def cadence(self) -> str:
        return "daily" if self.day is not None else "monthly"

    @property
    def zip_name(self) -> str:
        if self.day is None:
            return f"{self.symbol}-{self.timeframe}-{self.year:04d}-{self.month:02d}.zip"
        return (
            f"{self.symbol}-{self.timeframe}-"
            f"{self.year:04d}-{self.month:02d}-{self.day:02d}.zip"
        )

    @property
    def csv_name(self) -> str:
        return self.zip_name[:-4] + ".csv"

    @property
    def checksum_name(self) -> str:
        return self.zip_name + ".CHECKSUM"

    @property
    def zip_url(self) -> str:
        return _kline_url(self, self.zip_name)

    @property
    def checksum_url(self) -> str:
        return _kline_url(self, self.checksum_name)


@dataclass(frozen=True)
class VerifiedKlineArchive:
    ref: KlineArchiveRef
    zip_path: Path
    sha256: str
    csv_name: str

    def read_csv_bytes(self) -> bytes:
        """Read the single verified CSV member from the placed ZIP."""
        return _read_expected_csv(self.zip_path, self.csv_name)


def monthly_kline_archive_ref(
    symbol: str, timeframe: str, year: int, month: int
) -> KlineArchiveRef:
    return _build_ref(symbol, timeframe, year, month, day=None)


def daily_kline_archive_ref(
    symbol: str, timeframe: str, year: int, month: int, day: int
) -> KlineArchiveRef:
    return _build_ref(symbol, timeframe, year, month, day=day)


def parse_checksum_file(text: str, *, expected_zip_name: str) -> str:
    """Return lowercase SHA-256 hex from a sha256sum line."""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if len(lines) != 1:
        raise ArchiveZipError("checksum file must contain exactly one record")
    line = lines[0]
    if "*" in line:
        hash_part, _, name_part = line.partition("*")
        digest = hash_part.strip()
        filename = name_part.strip()
    else:
        parts = line.split()
        if len(parts) != 2:
            raise ArchiveZipError(f"invalid checksum format: {line!r}")
        digest, filename = parts
    if not _SHA256_RE.fullmatch(digest):
        raise ArchiveZipError("checksum digest is not a 64-character SHA-256 hex")
    if filename != expected_zip_name:
        raise ArchiveZipError(
            f"checksum filename {filename!r} does not match {expected_zip_name!r}"
        )
    return digest.lower()


def inspect_kline_zip(data: bytes, *, expected_csv_name: str) -> str:
    """Validate ZIP bytes and return the single expected CSV member name."""
    try:
        with ZipFile(io.BytesIO(data)) as archive:
            names = archive.namelist()
    except BadZipFile as exc:
        raise ArchiveZipError("corrupt zip archive") from exc
    if not names:
        raise ArchiveZipError("zip has no members")
    if len(names) != 1:
        raise ArchiveZipError(f"zip must contain exactly one member, got {len(names)}")
    name = names[0]
    _reject_unsafe_member_name(name)
    if name != expected_csv_name:
        raise ArchiveZipError(
            f"unexpected zip member {name!r}; expected {expected_csv_name!r}"
        )
    return name


def fetch_verified_kline_archive(
    ref: KlineArchiveRef,
    dest_dir: Path,
    *,
    client: ArchiveHttpClient,
) -> VerifiedKlineArchive:
    """Download checksum + ZIP to temp files, verify, then atomically place the ZIP."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / ref.zip_name
    tmp_zip = dest.with_name(dest.name + ".tmp")
    tmp_sum = dest_dir / (ref.checksum_name + ".tmp")

    try:
        checksum_resp = _require_http_ok(client.fetch(ref.checksum_url), ref.checksum_url)
        _validate_body_length(checksum_resp, ref.checksum_url)
        tmp_sum.write_bytes(checksum_resp.body)
        try:
            checksum_text = checksum_resp.body.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ArchiveZipError("checksum file is not utf-8") from exc
        expected_digest = parse_checksum_file(
            checksum_text,
            expected_zip_name=ref.zip_name,
        )

        zip_resp = _require_http_ok(client.fetch(ref.zip_url), ref.zip_url)
        _validate_body_length(zip_resp, ref.zip_url)
        tmp_zip.write_bytes(zip_resp.body)
        actual_digest = hashlib.sha256(zip_resp.body).hexdigest()
        if actual_digest != expected_digest:
            raise ArchiveZipError(
                f"sha256 mismatch for {ref.zip_name}: "
                f"expected {expected_digest}, got {actual_digest}"
            )
        csv_name = inspect_kline_zip(zip_resp.body, expected_csv_name=ref.csv_name)
        os.replace(tmp_zip, dest)
    except Exception:
        _unlink_if_exists(tmp_zip)
        _unlink_if_exists(tmp_sum)
        raise
    _unlink_if_exists(tmp_sum)
    return VerifiedKlineArchive(
        ref=ref,
        zip_path=dest,
        sha256=actual_digest,
        csv_name=csv_name,
    )


def _build_ref(
    symbol: str,
    timeframe: str,
    year: int,
    month: int,
    day: int | None,
) -> KlineArchiveRef:
    timeframe_ms(timeframe)
    normalized = symbol.replace("/", "").strip().upper()
    if not _SYMBOL_RE.fullmatch(normalized):
        raise ArchiveZipError(f"invalid archive symbol {symbol!r}")
    _validate_calendar(year, month, day)
    return KlineArchiveRef(
        symbol=normalized,
        timeframe=timeframe,
        year=year,
        month=month,
        day=day,
    )


def _validate_calendar(year: int, month: int, day: int | None) -> None:
    if month < 1 or month > 12:
        raise ArchiveZipError(f"invalid month {month}")
    if day is None:
        return
    try:
        datetime(year, month, day, tzinfo=UTC)
    except ValueError as exc:
        raise ArchiveZipError(f"invalid day {year:04d}-{month:02d}-{day:02d}") from exc


def _kline_url(ref: KlineArchiveRef, filename: str) -> str:
    return (
        f"{ARCHIVE_BASE}/{ref.cadence}/klines/{ref.symbol}/{ref.timeframe}/{filename}"
    )


def _require_http_ok(response: ArchiveHttpResponse, url: str) -> ArchiveHttpResponse:
    if response.status != 200:
        raise ArchiveZipError(f"http {response.status} for {url}")
    return response


def _validate_body_length(response: ArchiveHttpResponse, url: str) -> None:
    raw = None
    for key, value in response.headers.items():
        if key.lower() == "content-length":
            raw = value
            break
    if raw is None or raw == "":
        return
    try:
        expected = int(raw)
    except ValueError as exc:
        raise ArchiveZipError(f"invalid content-length for {url}") from exc
    if expected != len(response.body):
        raise ArchiveZipError(
            f"incomplete download for {url}: "
            f"content-length {expected}, got {len(response.body)} bytes"
        )


def _reject_unsafe_member_name(name: str) -> None:
    if not name or name.endswith("/") or name.endswith("\\"):
        raise ArchiveZipError(f"unsafe zip member {name!r}")
    if "\x00" in name:
        raise ArchiveZipError(f"unsafe zip member {name!r}")
    replaced = name.replace("\\", "/")
    if replaced.startswith("/") or replaced.startswith("//"):
        raise ArchiveZipError(f"absolute zip member {name!r}")
    path = Path(replaced)
    if path.is_absolute() or path.anchor:
        raise ArchiveZipError(f"absolute zip member {name!r}")
    if ".." in path.parts or ".." in replaced.split("/"):
        raise ArchiveZipError(f"path traversal in zip member {name!r}")


def _read_expected_csv(zip_path: Path, expected_csv_name: str) -> bytes:
    data = zip_path.read_bytes()
    inspect_kline_zip(data, expected_csv_name=expected_csv_name)
    with ZipFile(io.BytesIO(data)) as archive:
        return archive.read(expected_csv_name)


def _unlink_if_exists(path: Path) -> None:
    if path.exists():
        path.unlink()

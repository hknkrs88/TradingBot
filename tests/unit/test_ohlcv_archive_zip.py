from __future__ import annotations

import hashlib
import io
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from tradingbot.data.ohlcv.archive_zip import (
    ArchiveHttpResponse,
    ArchiveZipError,
    daily_kline_archive_ref,
    fetch_verified_kline_archive,
    inspect_kline_zip,
    monthly_kline_archive_ref,
    parse_checksum_file,
)


def _zip_bytes(member: str, payload: bytes = b"1,2,3,4,5,6,7,8,9,10,11,12\n") -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as archive:
        info = zipfile.ZipInfo(member)
        archive.writestr(info, payload)
    return buf.getvalue()


def _multi_zip(members: list[str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as archive:
        for name in members:
            archive.writestr(zipfile.ZipInfo(name), b"x")
    return buf.getvalue()


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _checksum_text(digest: str, filename: str) -> str:
    return f"{digest}  {filename}\n"


@dataclass
class FakeHttpClient:
    responses: dict[str, ArchiveHttpResponse]
    calls: list[str] = field(default_factory=list)

    def fetch(self, url: str) -> ArchiveHttpResponse:
        self.calls.append(url)
        if url not in self.responses:
            return ArchiveHttpResponse(status=404, body=b"", headers={})
        return self.responses[url]


def _ok_pair(ref, zip_bytes: bytes, *, zip_headers=None, sum_headers=None) -> FakeHttpClient:
    digest = _sha256(zip_bytes)
    return FakeHttpClient(
        {
            ref.checksum_url: ArchiveHttpResponse(
                200,
                _checksum_text(digest, ref.zip_name).encode("utf-8"),
                sum_headers or {"Content-Length": str(len(_checksum_text(digest, ref.zip_name)))},
            ),
            ref.zip_url: ArchiveHttpResponse(
                200,
                zip_bytes,
                zip_headers or {"Content-Length": str(len(zip_bytes))},
            ),
        }
    )


def test_monthly_and_daily_names_and_urls() -> None:
    monthly = monthly_kline_archive_ref("BTC/USDT", "1h", 2024, 12)
    assert monthly.symbol == "BTCUSDT"
    assert monthly.zip_name == "BTCUSDT-1h-2024-12.zip"
    assert monthly.csv_name == "BTCUSDT-1h-2024-12.csv"
    assert monthly.checksum_name == "BTCUSDT-1h-2024-12.zip.CHECKSUM"
    assert monthly.zip_url == (
        "https://data.binance.vision/data/spot/monthly/klines/"
        "BTCUSDT/1h/BTCUSDT-1h-2024-12.zip"
    )
    assert monthly.checksum_url == monthly.zip_url + ".CHECKSUM"

    daily = daily_kline_archive_ref("ETHUSDT", "4h", 2026, 10, 9)
    assert daily.zip_name == "ETHUSDT-4h-2026-10-09.zip"
    assert daily.zip_url == (
        "https://data.binance.vision/data/spot/daily/klines/"
        "ETHUSDT/4h/ETHUSDT-4h-2026-10-09.zip"
    )


def test_checksum_valid_name_and_digest() -> None:
    digest = "dfec812f73a84257b195cbdaed34d020fa77e845625c061c13bd6a60e7ef9372"
    assert (
        parse_checksum_file(
            f"{digest}  BTCUSDT-1h-2024-12.zip\n",
            expected_zip_name="BTCUSDT-1h-2024-12.zip",
        )
        == digest
    )


def test_checksum_wrong_digest_rejected(tmp_path: Path) -> None:
    ref = monthly_kline_archive_ref("BTCUSDT", "1h", 2024, 12)
    payload = _zip_bytes(ref.csv_name)
    client = FakeHttpClient(
        {
            ref.checksum_url: ArchiveHttpResponse(
                200,
                _checksum_text("0" * 64, ref.zip_name).encode(),
                {},
            ),
            ref.zip_url: ArchiveHttpResponse(200, payload, {}),
        }
    )
    dest = tmp_path / "keep.bin"
    dest.write_bytes(b"ORIGINAL")
    with pytest.raises(ArchiveZipError, match="sha256 mismatch"):
        fetch_verified_kline_archive(ref, tmp_path, client=client)
    assert dest.read_bytes() == b"ORIGINAL"
    assert not (tmp_path / ref.zip_name).exists()


def test_checksum_wrong_filename_rejected() -> None:
    digest = "a" * 64
    with pytest.raises(ArchiveZipError, match="checksum filename"):
        parse_checksum_file(
            f"{digest}  OTHER.zip\n",
            expected_zip_name="BTCUSDT-1h-2024-12.zip",
        )


def test_checksum_invalid_format_rejected() -> None:
    with pytest.raises(ArchiveZipError, match="invalid checksum format"):
        parse_checksum_file("not-a-checksum\n", expected_zip_name="BTCUSDT-1h-2024-12.zip")
    with pytest.raises(ArchiveZipError, match="exactly one record"):
        parse_checksum_file("\n\n", expected_zip_name="BTCUSDT-1h-2024-12.zip")
    with pytest.raises(ArchiveZipError, match="64-character SHA-256"):
        parse_checksum_file("abcd  BTCUSDT-1h-2024-12.zip\n", expected_zip_name="BTCUSDT-1h-2024-12.zip")


def test_corrupt_zip_rejected(tmp_path: Path) -> None:
    ref = monthly_kline_archive_ref("BTCUSDT", "1h", 2024, 12)
    garbage = b"not-a-zip"
    client = _ok_pair(ref, garbage)
    with pytest.raises(ArchiveZipError, match="corrupt zip"):
        fetch_verified_kline_archive(ref, tmp_path, client=client)
    assert not (tmp_path / ref.zip_name).exists()


def test_missing_csv_member_rejected() -> None:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w"):
        pass
    with pytest.raises(ArchiveZipError, match="no members"):
        inspect_kline_zip(buf.getvalue(), expected_csv_name="BTCUSDT-1h-2024-12.csv")


def test_multiple_zip_members_rejected() -> None:
    data = _multi_zip(["BTCUSDT-1h-2024-12.csv", "extra.csv"])
    with pytest.raises(ArchiveZipError, match="exactly one member"):
        inspect_kline_zip(data, expected_csv_name="BTCUSDT-1h-2024-12.csv")


def test_unexpected_member_name_rejected() -> None:
    data = _zip_bytes("other.csv")
    with pytest.raises(ArchiveZipError, match="unexpected zip member"):
        inspect_kline_zip(data, expected_csv_name="BTCUSDT-1h-2024-12.csv")


def test_path_traversal_member_rejected() -> None:
    data = _zip_bytes("../evil.csv")
    with pytest.raises(ArchiveZipError, match="path traversal"):
        inspect_kline_zip(data, expected_csv_name="BTCUSDT-1h-2024-12.csv")


def test_absolute_member_path_rejected() -> None:
    data = _zip_bytes("/tmp/evil.csv")
    with pytest.raises(ArchiveZipError, match="absolute zip member"):
        inspect_kline_zip(data, expected_csv_name="BTCUSDT-1h-2024-12.csv")
    windows = _zip_bytes("C:/evil.csv")
    with pytest.raises(ArchiveZipError, match="absolute zip member"):
        inspect_kline_zip(windows, expected_csv_name="BTCUSDT-1h-2024-12.csv")


def test_http_error_code(tmp_path: Path) -> None:
    ref = monthly_kline_archive_ref("BTCUSDT", "1h", 2024, 12)
    client = FakeHttpClient(
        {ref.checksum_url: ArchiveHttpResponse(404, b"missing", {})}
    )
    with pytest.raises(ArchiveZipError, match="http 404"):
        fetch_verified_kline_archive(ref, tmp_path, client=client)
    assert not (tmp_path / ref.zip_name).exists()
    assert list(tmp_path.glob("*.tmp")) == []


def test_incomplete_download(tmp_path: Path) -> None:
    ref = monthly_kline_archive_ref("BTCUSDT", "1h", 2024, 12)
    payload = _zip_bytes(ref.csv_name)
    digest = _sha256(payload)
    client = FakeHttpClient(
        {
            ref.checksum_url: ArchiveHttpResponse(
                200,
                _checksum_text(digest, ref.zip_name).encode(),
                {},
            ),
            ref.zip_url: ArchiveHttpResponse(
                200,
                payload[:10],
                {"Content-Length": str(len(payload))},
            ),
        }
    )
    with pytest.raises(ArchiveZipError, match="incomplete download"):
        fetch_verified_kline_archive(ref, tmp_path, client=client)
    assert list(tmp_path.glob("*.tmp")) == []
    assert not (tmp_path / ref.zip_name).exists()


def test_temp_files_removed_after_failure(tmp_path: Path) -> None:
    ref = monthly_kline_archive_ref("BTCUSDT", "1h", 2024, 12)
    client = FakeHttpClient(
        {ref.checksum_url: ArchiveHttpResponse(500, b"err", {})}
    )
    with pytest.raises(ArchiveZipError, match="http 500"):
        fetch_verified_kline_archive(ref, tmp_path, client=client)
    assert list(tmp_path.iterdir()) == []


def test_failed_verification_does_not_change_existing_dest(tmp_path: Path) -> None:
    ref = monthly_kline_archive_ref("BTCUSDT", "1h", 2024, 12)
    dest = tmp_path / ref.zip_name
    dest.write_bytes(b"KEEP-ME")
    client = FakeHttpClient(
        {
            ref.checksum_url: ArchiveHttpResponse(
                200,
                _checksum_text("b" * 64, ref.zip_name).encode(),
                {},
            ),
            ref.zip_url: ArchiveHttpResponse(200, _zip_bytes(ref.csv_name), {}),
        }
    )
    with pytest.raises(ArchiveZipError, match="sha256 mismatch"):
        fetch_verified_kline_archive(ref, tmp_path, client=client)
    assert dest.read_bytes() == b"KEEP-ME"
    assert list(tmp_path.glob("*.tmp")) == []


def test_successful_atomic_placement_and_csv_read(tmp_path: Path) -> None:
    ref = monthly_kline_archive_ref("BTCUSDT", "1h", 2024, 12)
    csv_payload = b"1733011200000,1,1,1,1,1,1733014799999,0,0,0,0,0\n"
    payload = _zip_bytes(ref.csv_name, csv_payload)
    client = _ok_pair(ref, payload)
    placed = fetch_verified_kline_archive(ref, tmp_path, client=client)
    dest = tmp_path / ref.zip_name
    assert placed.zip_path == dest
    assert dest.is_file()
    assert dest.read_bytes() == payload
    assert placed.sha256 == _sha256(payload)
    assert placed.read_csv_bytes() == csv_payload
    assert placed.checksum_verified is True
    assert placed.checksum_path == tmp_path / ref.checksum_name
    assert placed.checksum_path.is_file()
    assert placed.checksum_path.read_text(encoding="utf-8") == placed.checksum_text
    assert list(tmp_path.glob("*.tmp")) == []
    assert client.calls == [ref.checksum_url, ref.zip_url]


def test_daily_fetch_uses_daily_urls(tmp_path: Path) -> None:
    ref = daily_kline_archive_ref("XRPUSDT", "1h", 2026, 10, 9)
    payload = _zip_bytes(ref.csv_name)
    client = _ok_pair(ref, payload)
    placed = fetch_verified_kline_archive(ref, tmp_path, client=client)
    assert placed.ref.cadence == "daily"
    assert "daily/klines/XRPUSDT/1h/" in ref.zip_url
    assert placed.zip_path.name == "XRPUSDT-1h-2026-10-09.zip"

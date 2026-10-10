from __future__ import annotations

import hashlib
import io
import json
import os
import zipfile
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import pytest

from tradingbot.backtest.dataset import load_backtest_series
from tradingbot.data.io import sha256_file
from tradingbot.data.ohlcv.archive_import import (
    ArchiveImportError,
    ArchiveImportRequest,
    experimental_series_dir,
    import_archive_klines,
    resolve_current_generation,
)
from tradingbot.data.ohlcv.archive_provenance import (
    PROVENANCE_FILENAME,
    PROVENANCE_SCHEMA,
    ArchiveProvenanceError,
    build_provenance_payload,
    provenance_path,
    validate_provenance_file,
    validate_provenance_payload,
    validate_report_provenance_link,
    write_provenance,
)
from tradingbot.data.ohlcv.archive_zip import ArchiveHttpResponse, monthly_kline_archive_ref
from tradingbot.data.ohlcv.models import Candle
from tradingbot.data.ohlcv import reader as ohlcv_reader

HOUR = 3_600_000
NOW_MS = 2_000_000_000_000
DEC_START = int(datetime(2024, 12, 1, tzinfo=UTC).timestamp()) * 1000
JAN_START = int(datetime(2025, 1, 1, tzinfo=UTC).timestamp()) * 1000
FEB_START = int(datetime(2025, 2, 1, tzinfo=UTC).timestamp()) * 1000


@dataclass
class FakeHttpClient:
    responses: dict[str, ArchiveHttpResponse]
    calls: list[str] = field(default_factory=list)

    def fetch(self, url: str) -> ArchiveHttpResponse:
        self.calls.append(url)
        if url not in self.responses:
            return ArchiveHttpResponse(status=404, body=b"", headers={})
        return self.responses[url]


def _csv_line(open_ms: int) -> str:
    return f"{open_ms},1,1,1,1,1,{open_ms + HOUR - 1},0,0,0,0,0"


def _zip_csv(member: str, lines: list[str]) -> bytes:
    payload = ("\n".join(lines) + "\n").encode("utf-8")
    info = zipfile.ZipInfo(member)
    info.date_time = (1980, 1, 1, 0, 0, 0)
    info.compress_type = zipfile.ZIP_STORED
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as archive:
        archive.writestr(info, payload)
    return buf.getvalue()


def _checksum(zip_bytes: bytes, zip_name: str) -> bytes:
    digest = hashlib.sha256(zip_bytes).hexdigest()
    return f"{digest}  {zip_name}\n".encode()


def _add_ref(client: FakeHttpClient, ref, lines: list[str]) -> None:
    payload = _zip_csv(ref.csv_name, lines)
    client.responses[ref.checksum_url] = ArchiveHttpResponse(
        200, _checksum(payload, ref.zip_name), {}
    )
    client.responses[ref.zip_url] = ArchiveHttpResponse(200, payload, {})


def _request(tmp_path: Path, **over: object) -> ArchiveImportRequest:
    values: dict[str, object] = {
        "symbol": "BTCUSDT",
        "timeframe": "1h",
        "start_ms": DEC_START,
        "end_exclusive_ms": DEC_START + 3 * HOUR,
        "cadence": "monthly",
        "dest_root": tmp_path / "experimental",
        "now_ms": NOW_MS,
    }
    values.update(over)
    return ArchiveImportRequest(**values)  # type: ignore[arg-type]


def _series_dir(tmp_path: Path) -> Path:
    return experimental_series_dir(tmp_path / "experimental", "BTCUSDT", "1h")


def _live(tmp_path: Path) -> Path:
    return resolve_current_generation(_series_dir(tmp_path))


def _import_ok(tmp_path: Path, *, end_exclusive_ms: int = JAN_START):
    ref = monthly_kline_archive_ref("BTCUSDT", "1h", 2024, 12)
    client = FakeHttpClient({})
    _add_ref(client, ref, [_csv_line(DEC_START), _csv_line(DEC_START + HOUR)])
    report = import_archive_klines(
        _request(tmp_path, end_exclusive_ms=end_exclusive_ms),
        client=client,
    )
    return ref, report


def test_provenance_is_deterministic(tmp_path: Path) -> None:
    _import_ok(tmp_path)
    first = json.loads((_live(tmp_path) / PROVENANCE_FILENAME).read_text(encoding="utf-8"))
    _import_ok(tmp_path)
    second = json.loads((_live(tmp_path) / PROVENANCE_FILENAME).read_text(encoding="utf-8"))
    assert first == second
    assert first["archives"] == sorted(first["archives"], key=lambda item: item["zip_name"])


def test_provenance_records_zip_checksum_and_period(tmp_path: Path) -> None:
    ref, report = _import_ok(tmp_path)
    series = _live(tmp_path)
    payload = validate_provenance_file(series / PROVENANCE_FILENAME)
    assert payload["schema"] == PROVENANCE_SCHEMA
    assert payload["schema"] == "binance_archive_provenance/v2"
    assert payload["candles_sha256"] == sha256_file(series / "candles.json")
    assert payload["experimental"] is True
    assert payload["symbol"] == "BTCUSDT"
    assert payload["timeframe"] == "1h"
    assert payload["cadence"] == "monthly"
    assert payload["range_start_ms"] == DEC_START
    assert payload["range_end_exclusive_ms"] == JAN_START
    record = payload["archives"][0]
    assert record["zip_name"] == ref.zip_name
    assert record["csv_name"] == ref.csv_name
    assert record["checksum_name"] == ref.checksum_name
    assert record["checksum_verified"] is True
    assert record["period_start_ms"] == DEC_START
    assert record["period_end_exclusive_ms"] == JAN_START
    assert record["zip_sha256"] == report["periods"][0]["sha256"]
    checksum_file = series / "checksums" / ref.checksum_name
    assert checksum_file.is_file()
    assert checksum_file.read_text(encoding="utf-8") == record["checksum_text"]
    assert hashlib.sha256(checksum_file.read_bytes()).hexdigest() == record["checksum_sha256"]
    digest = sha256_file(series / PROVENANCE_FILENAME)
    assert report["provenance"]["sha256"] == digest
    assert report["provenance"]["path"] == PROVENANCE_FILENAME
    validate_report_provenance_link(report, series)


def test_missing_provenance_is_rejected(tmp_path: Path) -> None:
    _import_ok(tmp_path)
    series = _live(tmp_path)
    report = json.loads((series / "import_report.json").read_text(encoding="utf-8"))
    (series / PROVENANCE_FILENAME).unlink()
    with pytest.raises(ArchiveProvenanceError, match="missing"):
        validate_report_provenance_link(report, series)
    with pytest.raises(ArchiveImportError):
        resolve_current_generation(_series_dir(tmp_path))


def test_corrupt_provenance_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / PROVENANCE_FILENAME
    path.write_text("{not-json", encoding="utf-8")
    with pytest.raises(ArchiveProvenanceError, match="invalid provenance json"):
        validate_provenance_file(path)


def test_provenance_hash_mismatch_is_rejected(tmp_path: Path) -> None:
    _import_ok(tmp_path)
    series = _live(tmp_path)
    report = json.loads((series / "import_report.json").read_text(encoding="utf-8"))
    report["provenance"]["sha256"] = "0" * 64
    with pytest.raises(ArchiveProvenanceError, match="hash mismatch"):
        validate_report_provenance_link(report, series)


def test_import_report_period_mismatch_is_rejected(tmp_path: Path) -> None:
    _import_ok(tmp_path)
    series = _live(tmp_path)
    report = json.loads((series / "import_report.json").read_text(encoding="utf-8"))
    report["periods"][0]["sha256"] = "1" * 64
    with pytest.raises(ArchiveProvenanceError, match="zip sha256 mismatch"):
        validate_report_provenance_link(report, series)


def test_failed_import_keeps_previous_provenance(tmp_path: Path) -> None:
    dest = _series_dir(tmp_path)
    dest.mkdir(parents=True)
    (dest / "candles.json").write_text("PREV-CANDLES", encoding="utf-8")
    (dest / "import_report.json").write_text("PREV-REPORT", encoding="utf-8")
    (dest / PROVENANCE_FILENAME).write_text("PREV-PROVENANCE", encoding="utf-8")
    with pytest.raises(ArchiveImportError):
        import_archive_klines(
            _request(tmp_path, end_exclusive_ms=FEB_START),
            client=FakeHttpClient({}),
        )
    assert (dest / "candles.json").read_text(encoding="utf-8") == "PREV-CANDLES"
    assert (dest / "import_report.json").read_text(encoding="utf-8") == "PREV-REPORT"
    assert (dest / PROVENANCE_FILENAME).read_text(encoding="utf-8") == "PREV-PROVENANCE"


def test_publish_failure_leaves_legacy_and_does_not_flip_pointer(tmp_path: Path) -> None:
    dest = _series_dir(tmp_path)
    dest.mkdir(parents=True)
    (dest / "candles.json").write_text("PREV-CANDLES", encoding="utf-8")
    (dest / "import_report.json").write_text("PREV-REPORT", encoding="utf-8")
    (dest / PROVENANCE_FILENAME).write_text("PREV-PROVENANCE", encoding="utf-8")
    ref = monthly_kline_archive_ref("BTCUSDT", "1h", 2024, 12)
    client = FakeHttpClient({})
    _add_ref(client, ref, [_csv_line(DEC_START)])

    def flaky(src: Path, dst: Path) -> None:
        if dst.name == "import_report.json":
            raise OSError("report replace failed")
        os.replace(src, dst)

    with pytest.raises(ArchiveImportError, match="current pointer unchanged"):
        import_archive_klines(
            _request(tmp_path, end_exclusive_ms=JAN_START),
            client=client,
            replace_fn=flaky,
        )
    assert dest.joinpath("candles.json").read_text(encoding="utf-8") == "PREV-CANDLES"
    assert dest.joinpath("import_report.json").read_text(encoding="utf-8") == "PREV-REPORT"
    assert dest.joinpath(PROVENANCE_FILENAME).read_text(encoding="utf-8") == "PREV-PROVENANCE"
    assert not dest.joinpath("current.json").is_file()
    with pytest.raises(ArchiveImportError):
        resolve_current_generation(dest)


def test_staging_temps_are_removed(tmp_path: Path) -> None:
    _import_ok(tmp_path)
    series = _series_dir(tmp_path)
    leftovers = list(series.parent.glob("*.staging-*")) + list(series.rglob("*.tmp"))
    assert leftovers == []


def test_unsupported_provenance_schema_rejected() -> None:
    payload = build_provenance_payload(
        symbol="BTCUSDT",
        timeframe="1h",
        cadence="monthly",
        range_start_ms=DEC_START,
        range_end_exclusive_ms=JAN_START,
        archives=[],
    )
    payload["schema"] = "nope"
    with pytest.raises(ArchiveProvenanceError, match="unsupported provenance schema"):
        validate_provenance_payload(payload)


def test_v1_provenance_schema_is_rejected() -> None:
    payload = build_provenance_payload(
        symbol="BTCUSDT",
        timeframe="1h",
        cadence="monthly",
        range_start_ms=DEC_START,
        range_end_exclusive_ms=JAN_START,
        archives=[],
        candles_sha256="0" * 64,
    )
    payload["schema"] = "binance_archive_provenance/v1"
    with pytest.raises(ArchiveProvenanceError, match="unsupported provenance schema"):
        validate_provenance_payload(payload)


def test_missing_candles_sha256_is_rejected() -> None:
    payload = build_provenance_payload(
        symbol="BTCUSDT",
        timeframe="1h",
        cadence="monthly",
        range_start_ms=DEC_START,
        range_end_exclusive_ms=JAN_START,
        archives=[],
    )
    with pytest.raises(ArchiveProvenanceError, match="candles"):
        validate_provenance_payload(payload)


def test_write_provenance_returns_file_digest(tmp_path: Path) -> None:
    payload = build_provenance_payload(
        symbol="BTCUSDT",
        timeframe="1h",
        cadence="monthly",
        range_start_ms=DEC_START,
        range_end_exclusive_ms=JAN_START,
        archives=[],
        candles_sha256="0" * 64,
    )
    path = tmp_path / PROVENANCE_FILENAME
    digest = write_provenance(path, payload)
    assert digest == sha256_file(path)
    assert digest == sha256_file(path)


def test_candle_model_and_reader_unchanged() -> None:
    candle = Candle(timestamp=0, open=1.0, high=1.0, low=1.0, close=1.0, volume=1.0)
    assert type(candle.open) is float
    assert candle.to_dict()["open"] == 1.0
    assert hasattr(ohlcv_reader, "load_processed")
    assert callable(load_backtest_series)
    assert provenance_path(Path("x")).name == PROVENANCE_FILENAME

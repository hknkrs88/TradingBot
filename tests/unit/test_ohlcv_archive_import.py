from __future__ import annotations

import hashlib
import io
import json
import zipfile
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import pytest

from tradingbot.data.ohlcv.archive_import import (
    ArchiveImportError,
    ArchiveImportRequest,
    _combine_parsed_groups,
    experimental_series_dir,
    import_archive_klines,
    select_archive_refs,
)
from tradingbot.data.ohlcv.archive_zip import ArchiveHttpResponse, monthly_kline_archive_ref
from tradingbot.data.ohlcv.models import Candle

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


def _csv_line(open_ms: int, *, us: bool = False, volume: str = "1") -> str:
    if us:
        open_raw = open_ms * 1000
        close_raw = open_raw + HOUR * 1000 - 1
    else:
        open_raw = open_ms
        close_raw = open_ms + HOUR - 1
    return f"{open_raw},1,1,1,1,{volume},{close_raw},0,0,0,0,0"


def _zip_csv(member: str, lines: list[str]) -> bytes:
    payload = ("\n".join(lines) + "\n").encode("utf-8")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as archive:
        archive.writestr(member, payload)
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


def test_monthly_range_selects_single_month() -> None:
    refs = select_archive_refs("BTC/USDT", "1h", DEC_START, DEC_START + 3 * HOUR, "monthly")
    assert [ref.zip_name for ref in refs] == ["BTCUSDT-1h-2024-12.zip"]


def test_monthly_range_crossing_month_boundary() -> None:
    refs = select_archive_refs("BTCUSDT", "1h", DEC_START, FEB_START, "monthly")
    assert [ref.zip_name for ref in refs] == [
        "BTCUSDT-1h-2024-12.zip",
        "BTCUSDT-1h-2025-01.zip",
    ]


def test_daily_range_selects_intersecting_days() -> None:
    refs = select_archive_refs(
        "ETHUSDT", "1h", DEC_START, DEC_START + 36 * HOUR, "daily"
    )
    assert [ref.zip_name for ref in refs] == [
        "ETHUSDT-1h-2024-12-01.zip",
        "ETHUSDT-1h-2024-12-02.zip",
    ]


def test_empty_range_rejected() -> None:
    with pytest.raises(ArchiveImportError, match="non-empty"):
        select_archive_refs("BTCUSDT", "1h", DEC_START, DEC_START, "monthly")


def test_invalid_symbol_and_timeframe() -> None:
    with pytest.raises(ArchiveImportError, match="invalid archive symbol"):
        select_archive_refs("btc usdt", "1h", DEC_START, JAN_START, "monthly")
    with pytest.raises(ArchiveImportError, match="unsupported timeframe"):
        select_archive_refs("BTCUSDT", "15m", DEC_START, JAN_START, "monthly")


def test_start_inclusive_end_exclusive(tmp_path: Path) -> None:
    ref = monthly_kline_archive_ref("BTCUSDT", "1h", 2024, 12)
    client = FakeHttpClient({})
    lines = [_csv_line(DEC_START + i * HOUR) for i in range(5)]
    _add_ref(client, ref, lines)
    report = import_archive_klines(
        _request(tmp_path, end_exclusive_ms=DEC_START + 3 * HOUR),
        client=client,
    )
    assert report["status"] == "success"
    assert report["counts"]["parsed"] == 5
    assert report["counts"]["in_range"] == 3
    assert report["counts"]["out_of_range"] == 2
    series_dir = experimental_series_dir(tmp_path / "experimental", "BTCUSDT", "1h")
    payload = json.loads((series_dir / "candles.json").read_text(encoding="utf-8"))
    stamps = [row["timestamp"] for row in payload["candles"]]
    assert stamps == [DEC_START, DEC_START + HOUR, DEC_START + 2 * HOUR]
    assert DEC_START + 3 * HOUR not in stamps
    assert payload["experimental"] is True


def test_successful_zip_csv_experimental_series(tmp_path: Path) -> None:
    ref = monthly_kline_archive_ref("BTCUSDT", "1h", 2024, 12)
    client = FakeHttpClient({})
    _add_ref(client, ref, [_csv_line(DEC_START), _csv_line(DEC_START + HOUR)])
    report = import_archive_klines(_request(tmp_path, end_exclusive_ms=JAN_START), client=client)
    assert report["schema"] == "binance_archive_import_report/v1"
    assert report["experimental"] is True
    assert report["periods"][0]["zip_name"] == "BTCUSDT-1h-2024-12.zip"
    assert report["series"]["candle_count"] == 2
    assert report["series"]["first_timestamp"] == DEC_START
    assert (tmp_path / "experimental" / "BTCUSDT" / "1h" / "import_report.json").is_file()


def test_missing_archive_http_error_reported(tmp_path: Path) -> None:
    dec = monthly_kline_archive_ref("BTCUSDT", "1h", 2024, 12)
    jan = monthly_kline_archive_ref("BTCUSDT", "1h", 2025, 1)
    client = FakeHttpClient({})
    _add_ref(client, dec, [_csv_line(DEC_START)])
    report = import_archive_klines(
        _request(tmp_path, end_exclusive_ms=FEB_START),
        client=client,
    )
    assert report["status"] == "completed_with_missing"
    assert jan.checksum_url in client.calls
    missing = [p for p in report["periods"] if p["status"] == "missing"]
    assert missing[0]["zip_name"] == "BTCUSDT-1h-2025-01.zip"
    assert report["series"]["candle_count"] == 1


def test_corrupt_csv_fails_closed(tmp_path: Path) -> None:
    ref = monthly_kline_archive_ref("BTCUSDT", "1h", 2024, 12)
    client = FakeHttpClient({})
    _add_ref(client, ref, ["not,a,valid,row"])
    dest = experimental_series_dir(tmp_path / "experimental", "BTCUSDT", "1h")
    dest.mkdir(parents=True)
    marker = dest / "candles.json"
    marker.write_text("KEEP", encoding="utf-8")
    with pytest.raises(ArchiveImportError, match="failed"):
        import_archive_klines(_request(tmp_path), client=client)
    assert marker.read_text(encoding="utf-8") == "KEEP"


def test_duplicate_timestamps_fail_closed(tmp_path: Path) -> None:
    ref = monthly_kline_archive_ref("BTCUSDT", "1h", 2024, 12)
    client = FakeHttpClient({})
    _add_ref(client, ref, [_csv_line(DEC_START), _csv_line(DEC_START)])
    with pytest.raises(ArchiveImportError, match="duplicate timestamps"):
        import_archive_klines(_request(tmp_path), client=client)
    assert not experimental_series_dir(tmp_path / "experimental", "BTCUSDT", "1h").exists()


def test_conflicting_timestamps_across_archives() -> None:
    left = Candle(timestamp=DEC_START, open=1, high=1, low=1, close=1, volume=1)
    right = Candle(timestamp=DEC_START, open=2, high=2, low=2, close=2, volume=2)
    with pytest.raises(ArchiveImportError, match="duplicate timestamps"):
        _combine_parsed_groups([[left], [right]])


def test_deterministic_sort_and_rerun(tmp_path: Path) -> None:
    ref = monthly_kline_archive_ref("BTCUSDT", "1h", 2024, 12)
    client = FakeHttpClient({})
    _add_ref(
        client,
        ref,
        [_csv_line(DEC_START + HOUR), _csv_line(DEC_START)],
    )
    first = import_archive_klines(_request(tmp_path, end_exclusive_ms=JAN_START), client=client)
    second = import_archive_klines(_request(tmp_path, end_exclusive_ms=JAN_START), client=client)
    assert first["series"] == second["series"]
    payload = json.loads(
        (tmp_path / "experimental" / "BTCUSDT" / "1h" / "candles.json").read_text(
            encoding="utf-8"
        )
    )
    assert [row["timestamp"] for row in payload["candles"]] == [DEC_START, DEC_START + HOUR]


def test_out_of_range_excluded_and_counted(tmp_path: Path) -> None:
    ref = monthly_kline_archive_ref("BTCUSDT", "1h", 2024, 12)
    client = FakeHttpClient({})
    _add_ref(client, ref, [_csv_line(DEC_START + i * HOUR) for i in range(4)])
    report = import_archive_klines(
        _request(tmp_path, start_ms=DEC_START + HOUR, end_exclusive_ms=DEC_START + 3 * HOUR),
        client=client,
    )
    assert report["counts"]["out_of_range"] == 2
    assert report["series"]["first_timestamp"] == DEC_START + HOUR
    assert report["series"]["last_timestamp"] == DEC_START + 2 * HOUR


def test_failed_run_preserves_previous_experimental_output(tmp_path: Path) -> None:
    dest = experimental_series_dir(tmp_path / "experimental", "BTCUSDT", "1h")
    dest.mkdir(parents=True)
    candles = dest / "candles.json"
    report = dest / "import_report.json"
    candles.write_text("PREV-CANDLES", encoding="utf-8")
    report.write_text("PREV-REPORT", encoding="utf-8")
    client = FakeHttpClient({})
    with pytest.raises(ArchiveImportError):
        import_archive_klines(_request(tmp_path, end_exclusive_ms=FEB_START), client=client)
    assert candles.read_text(encoding="utf-8") == "PREV-CANDLES"
    assert report.read_text(encoding="utf-8") == "PREV-REPORT"


def test_does_not_write_processed_or_manifest(tmp_path: Path) -> None:
    processed = tmp_path / "data" / "processed" / "ohlcv"
    processed.mkdir(parents=True)
    manifest = processed / "manifest_1h.json"
    series = processed / "BTCUSDT" / "1h.json"
    series.parent.mkdir(parents=True)
    manifest.write_text("MANIFEST", encoding="utf-8")
    series.write_text("PROCESSED", encoding="utf-8")
    ref = monthly_kline_archive_ref("BTCUSDT", "1h", 2024, 12)
    client = FakeHttpClient({})
    _add_ref(client, ref, [_csv_line(DEC_START)])
    import_archive_klines(
        _request(tmp_path, dest_root=tmp_path / "data" / "experimental" / "binance_archive"),
        client=client,
    )
    assert manifest.read_text(encoding="utf-8") == "MANIFEST"
    assert series.read_text(encoding="utf-8") == "PROCESSED"
    assert (tmp_path / "data" / "experimental" / "binance_archive" / "BTCUSDT" / "1h" / "candles.json").is_file()


def test_refuses_processed_dest(tmp_path: Path) -> None:
    with pytest.raises(ArchiveImportError, match="data/processed"):
        import_archive_klines(
            _request(tmp_path, dest_root=tmp_path / "data" / "processed" / "ohlcv"),
            client=FakeHttpClient({}),
        )


def test_all_periods_missing_fails_without_publish(tmp_path: Path) -> None:
    with pytest.raises(ArchiveImportError, match="no in-range candles"):
        import_archive_klines(_request(tmp_path), client=FakeHttpClient({}))
    assert not experimental_series_dir(tmp_path / "experimental", "BTCUSDT", "1h").exists()

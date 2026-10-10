from __future__ import annotations

import hashlib
import importlib.util
import io
import zipfile
from datetime import UTC, datetime
from pathlib import Path

import pytest

from tradingbot.data.ohlcv.archive_import import (
    CURRENT_FILENAME,
    ArchiveImportRequest,
    experimental_series_dir,
    import_archive_klines,
    resolve_archive_series_input,
    resolve_current_generation,
)
from tradingbot.data.ohlcv.archive_overlap import (
    ArchiveOverlapError,
    analyze_archive_processed_files,
)
from tradingbot.data.ohlcv.archive_zip import ArchiveHttpResponse, monthly_kline_archive_ref
from tradingbot.data.ohlcv.models import Candle
from tradingbot.data.ohlcv.store import dump_candles

HOUR = 3_600_000
NOW_MS = 2_000_000_000_000
DEC_START = int(datetime(2024, 12, 1, tzinfo=UTC).timestamp()) * 1000
JAN_START = int(datetime(2025, 1, 1, tzinfo=UTC).timestamp()) * 1000


class FakeHttpClient:
    def __init__(self, responses: dict[str, ArchiveHttpResponse]) -> None:
        self.responses = responses

    def fetch(self, url: str) -> ArchiveHttpResponse:
        return self.responses.get(url, ArchiveHttpResponse(status=404, body=b"", headers={}))


def _load_overlap_cli():
    script = Path(__file__).resolve().parents[2] / "scripts" / "analyze_binance_archive_overlap.py"
    spec = importlib.util.spec_from_file_location("analyze_binance_archive_overlap", script)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _seed_processed(root: Path) -> None:
    processed_dir = root / "data" / "processed" / "ohlcv" / "BTCUSDT"
    processed_dir.mkdir(parents=True)
    dump_candles(
        processed_dir / "1h.json",
        [Candle(timestamp=DEC_START, open=1, high=1, low=1, close=1, volume=1)],
        symbol="BTC/USDT",
        timeframe="1h",
    )


def test_overlap_cli_file_path_is_used_as_is(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _seed_processed(tmp_path)
    archive_path = tmp_path / "explicit" / "candles.json"
    dump_candles(
        archive_path,
        [Candle(timestamp=DEC_START, open=1, high=1, low=1, close=1, volume=1)],
        symbol="BTCUSDT",
        timeframe="1h",
    )
    report_path = tmp_path / "reports" / "overlap-file.json"
    mod = _load_overlap_cli()
    rc = mod.main(
        [
            "--root",
            str(tmp_path),
            "--symbol",
            "BTCUSDT",
            "--timeframe",
            "1h",
            "--archive-series",
            str(archive_path),
            "--report",
            str(report_path),
        ]
    )
    assert rc == 0
    assert "status=matched" in capsys.readouterr().out
    assert report_path.is_file()
    assert archive_path.is_file()


def test_overlap_cli_help_keeps_file_path_contract(capsys: pytest.CaptureFixture[str]) -> None:
    mod = _load_overlap_cli()
    with pytest.raises(SystemExit) as caught:
        mod.main(["--help"])
    assert caught.value.code == 0
    help_text = capsys.readouterr().out
    assert "used as-is" in help_text
    assert "current.json" in help_text
    assert "Legacy/leftover" in help_text
    assert "rejected" in help_text
    assert "same rules as promote" in help_text


def test_overlap_cli_resolves_generation_series_root(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _seed_processed(tmp_path)
    ref = monthly_kline_archive_ref("BTCUSDT", "1h", 2024, 12)
    line = f"{DEC_START},1,1,1,1,1,{DEC_START + HOUR - 1},0,0,0,0,0"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as archive:
        archive.writestr(ref.csv_name, (line + "\n").encode())
    payload = buf.getvalue()
    digest = hashlib.sha256(payload).hexdigest()
    client = FakeHttpClient(
        {
            ref.checksum_url: ArchiveHttpResponse(200, f"{digest}  {ref.zip_name}\n".encode(), {}),
            ref.zip_url: ArchiveHttpResponse(200, payload, {}),
        }
    )
    dest_root = tmp_path / "experimental"
    import_archive_klines(
        ArchiveImportRequest(
            symbol="BTCUSDT",
            timeframe="1h",
            start_ms=DEC_START,
            end_exclusive_ms=JAN_START,
            cadence="monthly",
            dest_root=dest_root,
            now_ms=NOW_MS,
        ),
        client=client,
    )
    series = experimental_series_dir(dest_root, "BTCUSDT", "1h")
    live_candles = resolve_current_generation(series) / "candles.json"
    (series / "candles.json").write_text("LEGACY-NOT-LIVE", encoding="utf-8")
    resolved = resolve_archive_series_input(series)
    assert resolved == live_candles
    assert resolved.read_text(encoding="utf-8") != "LEGACY-NOT-LIVE"
    report_path = tmp_path / "reports" / "overlap-generation.json"
    mod = _load_overlap_cli()
    rc = mod.main(
        [
            "--root",
            str(tmp_path),
            "--symbol",
            "BTCUSDT",
            "--timeframe",
            "1h",
            "--archive-series",
            str(series),
            "--report",
            str(report_path),
        ]
    )
    assert rc == 0
    printed = capsys.readouterr().out
    assert "status=matched" in printed
    assert report_path.is_file()


def test_overlap_cli_rejects_legacy_series_root(tmp_path: Path) -> None:
    dest = tmp_path / "experimental" / "BTCUSDT" / "1h"
    dest.mkdir(parents=True)
    (dest / "candles.json").write_text("LEGACY", encoding="utf-8")
    mod = _load_overlap_cli()
    with pytest.raises(ArchiveOverlapError, match="legacy root candles.json is not live"):
        mod.main(
            [
                "--root",
                str(tmp_path),
                "--symbol",
                "BTCUSDT",
                "--timeframe",
                "1h",
                "--archive-series",
                str(dest),
                "--report",
                str(tmp_path / "reports" / "should-not-exist.json"),
            ]
        )
    assert not (tmp_path / "reports" / "should-not-exist.json").exists()
    assert (dest / "candles.json").read_text(encoding="utf-8") == "LEGACY"


def _import_generation(tmp_path: Path) -> Path:
    ref = monthly_kline_archive_ref("BTCUSDT", "1h", 2024, 12)
    line = f"{DEC_START},1,1,1,1,1,{DEC_START + HOUR - 1},0,0,0,0,0"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as archive:
        archive.writestr(ref.csv_name, (line + "\n").encode())
    payload = buf.getvalue()
    digest = hashlib.sha256(payload).hexdigest()
    client = FakeHttpClient(
        {
            ref.checksum_url: ArchiveHttpResponse(200, f"{digest}  {ref.zip_name}\n".encode(), {}),
            ref.zip_url: ArchiveHttpResponse(200, payload, {}),
        }
    )
    dest_root = tmp_path / "experimental"
    import_archive_klines(
        ArchiveImportRequest(
            symbol="BTCUSDT",
            timeframe="1h",
            start_ms=DEC_START,
            end_exclusive_ms=JAN_START,
            cadence="monthly",
            dest_root=dest_root,
            now_ms=NOW_MS,
        ),
        client=client,
    )
    return experimental_series_dir(dest_root, "BTCUSDT", "1h")


def test_overlap_library_standalone_candles_file(tmp_path: Path) -> None:
    _seed_processed(tmp_path)
    archive_path = tmp_path / "explicit" / "candles.json"
    dump_candles(
        archive_path,
        [Candle(timestamp=DEC_START, open=1, high=1, low=1, close=1, volume=1)],
        symbol="BTCUSDT",
        timeframe="1h",
    )
    report = analyze_archive_processed_files(
        root=tmp_path,
        symbol="BTCUSDT",
        timeframe="1h",
        archive_series_path=archive_path,
        report_path=tmp_path / "reports" / "overlap-standalone.json",
    )
    assert report["status"] == "matched"
    assert report["sources"]["archive"] == archive_path.as_posix()


def test_overlap_library_resolves_series_root_and_generation_dir(tmp_path: Path) -> None:
    _seed_processed(tmp_path)
    series = _import_generation(tmp_path)
    live = resolve_current_generation(series)
    expected = (live / "candles.json").as_posix()
    leftover = series / "candles.json"
    leftover.write_text("LEGACY-NOT-LIVE", encoding="utf-8")
    for source in (series, live, live / "candles.json"):
        report = analyze_archive_processed_files(
            root=tmp_path,
            symbol="BTCUSDT",
            timeframe="1h",
            archive_series_path=source,
            report_path=tmp_path / "reports" / f"overlap-{source.name}.json",
        )
        assert report["status"] == "matched"
        assert report["sources"]["archive"] == expected
    assert leftover.read_text(encoding="utf-8") == "LEGACY-NOT-LIVE"


def test_overlap_library_rejects_leftover_root_file(tmp_path: Path) -> None:
    _seed_processed(tmp_path)
    series = _import_generation(tmp_path)
    leftover = series / "candles.json"
    leftover.write_text("LEGACY", encoding="utf-8")
    with pytest.raises(ArchiveOverlapError, match="archive series input rejected:.*legacy root"):
        analyze_archive_processed_files(
            root=tmp_path,
            symbol="BTCUSDT",
            timeframe="1h",
            archive_series_path=leftover,
            report_path=tmp_path / "reports" / "should-not-exist.json",
        )
    assert not (tmp_path / "reports" / "should-not-exist.json").exists()
    assert leftover.read_text(encoding="utf-8") == "LEGACY"


def test_overlap_cli_rejects_leftover_root_file(tmp_path: Path) -> None:
    series = _import_generation(tmp_path)
    leftover = series / "candles.json"
    leftover.write_text("LEGACY", encoding="utf-8")
    mod = _load_overlap_cli()
    with pytest.raises(ArchiveOverlapError, match="archive series input rejected:.*legacy root"):
        mod.main(
            [
                "--root",
                str(tmp_path),
                "--symbol",
                "BTCUSDT",
                "--timeframe",
                "1h",
                "--archive-series",
                str(leftover),
                "--report",
                str(tmp_path / "reports" / "should-not-exist.json"),
            ]
        )
    assert leftover.read_text(encoding="utf-8") == "LEGACY"


def test_overlap_rejects_corrupt_pointer(tmp_path: Path) -> None:
    series = _import_generation(tmp_path)
    (series / CURRENT_FILENAME).write_text("{bad", encoding="utf-8")
    with pytest.raises(ArchiveOverlapError, match="archive series input rejected"):
        analyze_archive_processed_files(
            root=tmp_path,
            symbol="BTCUSDT",
            timeframe="1h",
            archive_series_path=series,
            report_path=tmp_path / "reports" / "should-not-exist.json",
        )


def test_overlap_rejects_missing_checksum_and_candles_hash_mismatch(tmp_path: Path) -> None:
    series = _import_generation(tmp_path)
    live = resolve_current_generation(series)
    checksum = next((live / "checksums").iterdir())
    checksum.unlink()
    with pytest.raises(ArchiveOverlapError, match="checksum missing"):
        analyze_archive_processed_files(
            root=tmp_path,
            symbol="BTCUSDT",
            timeframe="1h",
            archive_series_path=series,
            report_path=tmp_path / "reports" / "should-not-exist.json",
        )

    series = _import_generation(tmp_path / "hash")
    live = resolve_current_generation(
        experimental_series_dir(tmp_path / "hash" / "experimental", "BTCUSDT", "1h")
    )
    candles = live / "candles.json"
    candles.write_text(candles.read_text(encoding="utf-8") + " ", encoding="utf-8")
    with pytest.raises(ArchiveOverlapError, match="candles hash mismatch"):
        analyze_archive_processed_files(
            root=tmp_path / "hash",
            symbol="BTCUSDT",
            timeframe="1h",
            archive_series_path=experimental_series_dir(
                tmp_path / "hash" / "experimental", "BTCUSDT", "1h"
            ),
            report_path=tmp_path / "hash" / "reports" / "should-not-exist.json",
        )

    series = _import_generation(tmp_path / "prov")
    live = resolve_current_generation(
        experimental_series_dir(tmp_path / "prov" / "experimental", "BTCUSDT", "1h")
    )
    (live / "provenance.json").unlink()
    with pytest.raises(ArchiveOverlapError, match="archive series input rejected"):
        analyze_archive_processed_files(
            root=tmp_path / "prov",
            symbol="BTCUSDT",
            timeframe="1h",
            archive_series_path=experimental_series_dir(
                tmp_path / "prov" / "experimental", "BTCUSDT", "1h"
            ),
            report_path=tmp_path / "prov" / "reports" / "should-not-exist.json",
        )

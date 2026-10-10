from __future__ import annotations

import hashlib
import io
import json
import zipfile
from datetime import UTC, datetime
from pathlib import Path

import pytest

from tradingbot.data.io import atomic_write_json, sha256_file
from tradingbot.data.ohlcv.archive_import import (
    CURRENT_FILENAME,
    ArchiveImportRequest,
    experimental_series_dir,
    import_archive_klines,
    resolve_archive_series_input,
    resolve_current_generation,
)
from tradingbot.data.ohlcv.archive_promote import (
    ArchivePromoteError,
    ArchivePromoteRequest,
    promote_archive_series,
)
from tradingbot.data.ohlcv.archive_zip import ArchiveHttpResponse, monthly_kline_archive_ref
from tradingbot.data.ohlcv.models import Candle
from tradingbot.data.ohlcv.store import dump_candles, load_candles

HOUR = 3_600_000
NOW_MS = 2_000_000_000_000
DEC_START = int(datetime(2024, 12, 1, tzinfo=UTC).timestamp()) * 1000
JAN_START = int(datetime(2025, 1, 1, tzinfo=UTC).timestamp()) * 1000
SCHEMA = "ohlcv_dataset_manifest/v1"


class FakeHttpClient:
    def __init__(self, responses: dict[str, ArchiveHttpResponse]) -> None:
        self.responses = responses

    def fetch(self, url: str) -> ArchiveHttpResponse:
        return self.responses.get(url, ArchiveHttpResponse(status=404, body=b"", headers={}))


def _c(ts: int, close: float = 1.0) -> Candle:
    return Candle(timestamp=ts, open=close, high=close, low=close, close=close, volume=1)


def _write_processed(root: Path, candles: list[Candle]) -> Path:
    path = root / "data" / "processed" / "ohlcv" / "BTCUSDT" / "1h.json"
    dump_candles(path, candles, symbol="BTC/USDT", timeframe="1h")
    return path


def _write_manifest(root: Path, series_path: Path) -> Path:
    universe = root / "data" / "processed" / "universe.json"
    atomic_write_json(universe, {"universe": [{"binance_symbol": "BTC/USDT"}]})
    candles = load_candles(series_path)
    payload = {
        "schema": SCHEMA,
        "generated_at": "2026-10-06T17:00:00+00:00",
        "provider": "binance_public",
        "timeframe": "1h",
        "universe": {
            "path": "data/processed/universe.json",
            "sha256": sha256_file(universe),
            "symbol_count": 1,
            "symbols": ["BTC/USDT"],
        },
        "config": {"provider": "binance_public", "candle_limit": 1000, "drop_incomplete": True},
        "series": [
            {
                "symbol": "BTC/USDT",
                "path": "BTCUSDT/1h.json",
                "range_start": candles[0].timestamp,
                "range_end": candles[-1].timestamp,
                "candle_count": len(candles),
                "sha256": sha256_file(series_path),
            }
        ],
        "incomplete_symbols": [],
    }
    path = root / "data" / "processed" / "ohlcv" / "manifest_1h.json"
    atomic_write_json(path, payload)
    return path


def _seed_processed(root: Path, candles: list[Candle]) -> None:
    series = _write_processed(root, candles)
    _write_manifest(root, series)


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


def _req(tmp_path: Path, archive: Path) -> ArchivePromoteRequest:
    return ArchivePromoteRequest(
        root=tmp_path,
        symbol="BTCUSDT",
        timeframe="1h",
        archive_series_path=archive,
        report_path=tmp_path / "reports" / "promote.json",
        apply=False,
        accept_json_decimal=False,
    )


def _run_cli(tmp_path: Path, archive: Path) -> tuple[int, str, str]:
    import os
    import subprocess
    import sys

    repo = Path(__file__).resolve().parents[2]
    env = os.environ.copy()
    env["PYTHONPATH"] = str(repo / "src") + os.pathsep + env.get("PYTHONPATH", "")
    result = subprocess.run(
        [
            sys.executable,
            str(repo / "scripts" / "promote_binance_archive.py"),
            "--root",
            str(tmp_path),
            "--symbol",
            "BTCUSDT",
            "--timeframe",
            "1h",
            "--archive-series",
            str(archive),
            "--report",
            str(tmp_path / "reports" / "promote-cli.json"),
        ],
        cwd=str(repo),
        env=env,
        capture_output=True,
        check=False,
        text=True,
    )
    return result.returncode, result.stdout, result.stderr


def test_standalone_candles_file_remains_valid_promote_input(tmp_path: Path) -> None:
    candles = [_c(0), _c(HOUR), _c(2 * HOUR)]
    _seed_processed(tmp_path, [_c(HOUR), _c(2 * HOUR)])
    archive = tmp_path / "exp" / "candles.json"
    dump_candles(archive, candles, symbol="BTCUSDT", timeframe="1h")
    report = promote_archive_series(_req(tmp_path, archive))
    assert report["status"] == "dry_run"
    assert report["files_changed"] is False
    assert report["sources"]["archive"] == archive.as_posix()
    assert resolve_archive_series_input(archive) == archive


def test_promote_resolves_series_root_and_generation_dir(tmp_path: Path) -> None:
    _seed_processed(tmp_path, [_c(DEC_START)])
    series = _import_generation(tmp_path)
    live = resolve_current_generation(series)
    expected = (live / "candles.json").as_posix()
    leftover = series / "candles.json"
    leftover.write_text("LEGACY-NOT-LIVE", encoding="utf-8")

    for source in (series, live, live / "candles.json"):
        report = promote_archive_series(_req(tmp_path, source))
        assert report["status"] == "dry_run"
        assert report["sources"]["archive"] == expected
        assert leftover.read_text(encoding="utf-8") == "LEGACY-NOT-LIVE"


def test_promote_rejects_leftover_root_candles_file(tmp_path: Path) -> None:
    _seed_processed(tmp_path, [_c(DEC_START)])
    series = _import_generation(tmp_path)
    leftover = series / "candles.json"
    leftover.write_text("LEGACY", encoding="utf-8")
    with pytest.raises(ArchivePromoteError, match="legacy root candles.json is not live"):
        promote_archive_series(_req(tmp_path, leftover))
    assert leftover.read_text(encoding="utf-8") == "LEGACY"


def test_promote_rejects_legacy_series_root_without_pointer(tmp_path: Path) -> None:
    _seed_processed(tmp_path, [_c(HOUR)])
    dest = tmp_path / "experimental" / "BTCUSDT" / "1h"
    dest.mkdir(parents=True)
    leftover = dest / "candles.json"
    dump_candles(leftover, [_c(0)], symbol="BTCUSDT", timeframe="1h")
    with pytest.raises(ArchivePromoteError, match="legacy root candles.json is not live"):
        promote_archive_series(_req(tmp_path, dest))


def test_promote_rejects_corrupt_pointer_and_integrity_failures(tmp_path: Path) -> None:
    _seed_processed(tmp_path, [_c(DEC_START)])
    series = _import_generation(tmp_path)
    live = resolve_current_generation(series)

    pointer = series / CURRENT_FILENAME
    pointer.write_text("{bad", encoding="utf-8")
    with pytest.raises(ArchivePromoteError, match="archive series input rejected"):
        promote_archive_series(_req(tmp_path, series))

    pointer.write_text(
        json.dumps(
            {
                "schema": "binance_archive_current/v1",
                "experimental": True,
                "generation": live.name,
                "path": live.name,
            }
        ),
        encoding="utf-8",
    )
    (live / "candles.json").write_text(
        (live / "candles.json").read_text(encoding="utf-8") + " ",
        encoding="utf-8",
    )
    with pytest.raises(ArchivePromoteError, match="candles hash mismatch"):
        promote_archive_series(_req(tmp_path, series))


def test_promote_rejects_missing_checksum_and_provenance(tmp_path: Path) -> None:
    _seed_processed(tmp_path, [_c(DEC_START)])
    series = _import_generation(tmp_path)
    live = resolve_current_generation(series)
    checksum = next((live / "checksums").iterdir())
    checksum.unlink()
    with pytest.raises(ArchivePromoteError, match="checksum missing"):
        promote_archive_series(_req(tmp_path, series))

    series = _import_generation(tmp_path / "mismatch")
    root = tmp_path / "mismatch"
    _seed_processed(root, [_c(DEC_START)])
    live = resolve_current_generation(
        experimental_series_dir(root / "experimental", "BTCUSDT", "1h")
    )
    checksum = next((live / "checksums").iterdir())
    zip_name = checksum.name.removesuffix(".CHECKSUM")
    checksum.write_text(f"{'0' * 64}  {zip_name}\n", encoding="utf-8")
    with pytest.raises(ArchivePromoteError, match="checksum zip sha256 mismatch"):
        promote_archive_series(
            ArchivePromoteRequest(
                root=root,
                symbol="BTCUSDT",
                timeframe="1h",
                archive_series_path=experimental_series_dir(root / "experimental", "BTCUSDT", "1h"),
                report_path=root / "reports" / "promote.json",
            )
        )

    series = _import_generation(tmp_path / "again")
    root = tmp_path / "again"
    _seed_processed(root, [_c(DEC_START)])
    live = resolve_current_generation(experimental_series_dir(root / "experimental", "BTCUSDT", "1h"))
    (live / "provenance.json").unlink()
    with pytest.raises(ArchivePromoteError, match="archive series input rejected"):
        promote_archive_series(
            ArchivePromoteRequest(
                root=root,
                symbol="BTCUSDT",
                timeframe="1h",
                archive_series_path=experimental_series_dir(root / "experimental", "BTCUSDT", "1h"),
                report_path=root / "reports" / "promote.json",
            )
        )


def test_promote_cli_and_library_share_validation(tmp_path: Path) -> None:
    _seed_processed(tmp_path, [_c(DEC_START)])
    series = _import_generation(tmp_path)
    live = resolve_current_generation(series)
    leftover = series / "candles.json"
    leftover.write_text("LEGACY", encoding="utf-8")

    lib = promote_archive_series(_req(tmp_path, series))
    code, out, err = _run_cli(tmp_path, series)
    assert code == 0
    assert "status=dry_run" in out
    assert lib["sources"]["archive"] == (live / "candles.json").as_posix()
    assert err == "" or "error:" not in err

    with pytest.raises(ArchivePromoteError, match="legacy root candles.json is not live"):
        promote_archive_series(_req(tmp_path, leftover))
    code, out, err = _run_cli(tmp_path, leftover)
    assert code == 1
    assert "legacy root candles.json is not live" in err
    assert leftover.read_text(encoding="utf-8") == "LEGACY"

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from tradingbot.backtest.dataset import load_backtest_series
from tradingbot.data.io import atomic_write_json, sha256_file
from tradingbot.data.ohlcv.archive_promote import (
    ArchivePromoteRequest,
    promote_archive_series,
)
from tradingbot.data.ohlcv.models import Candle
from tradingbot.data.ohlcv.store import dump_candles, load_candles

HOUR = 3_600_000
SCHEMA = "ohlcv_dataset_manifest/v1"


def _c(ts: int, close: float = 1.0) -> Candle:
    return Candle(timestamp=ts, open=close, high=close, low=close, close=close, volume=1)


def _write_universe(root: Path, symbols: list[str]) -> Path:
    path = root / "data" / "processed" / "universe.json"
    atomic_write_json(path, {"universe": [{"binance_symbol": item} for item in symbols]})
    return path


def _write_processed(root: Path, symbol: str, timeframe: str, candles: list[Candle]) -> Path:
    safe = symbol.replace("/", "")
    path = root / "data" / "processed" / "ohlcv" / safe / f"{timeframe}.json"
    dump_candles(path, candles, symbol=symbol, timeframe=timeframe)
    return path


def _write_manifest(root: Path, universe: Path, series_files: dict[str, Path], timeframe: str = "1h") -> Path:
    symbols = list(series_files)
    series = []
    for symbol, path in series_files.items():
        candles = load_candles(path)
        series.append(
            {
                "symbol": symbol,
                "path": f"{symbol.replace('/', '')}/{timeframe}.json",
                "range_start": candles[0].timestamp,
                "range_end": candles[-1].timestamp,
                "candle_count": len(candles),
                "sha256": sha256_file(path),
            }
        )
    payload = {
        "schema": SCHEMA,
        "generated_at": "2026-10-06T17:00:00+00:00",
        "provider": "binance_public",
        "timeframe": timeframe,
        "universe": {
            "path": "data/processed/universe.json",
            "sha256": sha256_file(universe),
            "symbol_count": len(symbols),
            "symbols": symbols,
        },
        "config": {"provider": "binance_public", "candle_limit": 1000, "drop_incomplete": True},
        "series": series,
        "incomplete_symbols": [],
    }
    path = root / "data" / "processed" / "ohlcv" / f"manifest_{timeframe}.json"
    atomic_write_json(path, payload)
    return path


def _setup(tmp_path: Path) -> dict[str, Path]:
    universe = _write_universe(tmp_path, ["BTC/USDT", "ETH/USDT"])
    btc = _write_processed(tmp_path, "BTC/USDT", "1h", [_c(HOUR), _c(2 * HOUR)])
    eth = _write_processed(tmp_path, "ETH/USDT", "1h", [_c(0), _c(HOUR)])
    manifest = _write_manifest(tmp_path, universe, {"BTC/USDT": btc, "ETH/USDT": eth})
    archive = tmp_path / "exp" / "candles.json"
    dump_candles(
        archive,
        [_c(0), _c(HOUR), _c(2 * HOUR)],
        symbol="BTCUSDT",
        timeframe="1h",
    )
    report = tmp_path / "exp" / "promote_report.json"
    return {
        "universe": universe,
        "btc": btc,
        "eth": eth,
        "manifest": manifest,
        "archive": archive,
        "report": report,
    }


def _journal_root(tmp_path: Path) -> Path:
    return tmp_path / "data" / "processed" / "ohlcv" / ".promote_journal"


def _journal_json(tmp_path: Path) -> Path:
    return next(_journal_root(tmp_path).rglob("journal.json"))


def _run_cli(
    tmp_path: Path, files: dict[str, Path], extra: list[str]
) -> subprocess.CompletedProcess[bytes]:
    repo = Path(__file__).resolve().parents[2]
    env = os.environ.copy()
    env["PYTHONPATH"] = str(repo / "src") + os.pathsep + env.get("PYTHONPATH", "")
    return subprocess.run(
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
            str(files["archive"]),
            "--report",
            str(files["report"]),
            *extra,
        ],
        cwd=str(repo),
        env=env,
        capture_output=True,
        check=False,
    )


def _req(tmp_path: Path, files: dict[str, Path], **over: object) -> ArchivePromoteRequest:
    values = {
        "root": tmp_path,
        "symbol": "BTCUSDT",
        "timeframe": "1h",
        "archive_series_path": files["archive"],
        "report_path": files["report"],
        "apply": False,
        "accept_json_decimal": False,
    }
    values.update(over)
    return ArchivePromoteRequest(**values)  # type: ignore[arg-type]


def test_dry_run_does_not_change_processed(tmp_path: Path) -> None:
    files = _setup(tmp_path)
    before_btc = files["btc"].read_bytes()
    before_eth = files["eth"].read_bytes()
    before_man = files["manifest"].read_bytes()
    report = promote_archive_series(_req(tmp_path, files))
    assert report["status"] == "dry_run"
    assert report["files_changed"] is False
    assert report["mode"] == "dry_run"
    assert files["btc"].read_bytes() == before_btc
    assert files["eth"].read_bytes() == before_eth
    assert files["manifest"].read_bytes() == before_man
    assert files["report"].is_file()


def test_apply_without_flag_does_not_promote(tmp_path: Path) -> None:
    files = _setup(tmp_path)
    before = files["btc"].read_bytes()
    report = promote_archive_series(_req(tmp_path, files, apply=True))
    assert report["status"] == "rejected"
    assert report["failure_reason"] == "json_decimal_not_original_csv"
    assert files["btc"].read_bytes() == before


def test_apply_adds_older_archive_and_keeps_newer_processed(tmp_path: Path) -> None:
    files = _setup(tmp_path)
    report = promote_archive_series(
        _req(tmp_path, files, apply=True, accept_json_decimal=True)
    )
    assert report["status"] == "applied"
    assert report["files_changed"] is True
    candles = load_candles(files["btc"])
    assert [c.timestamp for c in candles] == [0, HOUR, 2 * HOUR]
    assert report["counts"]["added_bars"] == 1
    assert report["counts"]["processed_preserved"] == 2


def test_matching_common_timestamps_are_deduped(tmp_path: Path) -> None:
    files = _setup(tmp_path)
    report = promote_archive_series(
        _req(tmp_path, files, apply=True, accept_json_decimal=True)
    )
    assert report["counts"]["common_matched"] == 2
    assert report["counts"]["processed_bars_after"] == 3


def test_ohlcv_mismatch_rejects(tmp_path: Path) -> None:
    files = _setup(tmp_path)
    dump_candles(
        files["archive"],
        [_c(0), _c(HOUR, close=9.0), _c(2 * HOUR)],
        symbol="BTCUSDT",
        timeframe="1h",
    )
    before = files["btc"].read_bytes()
    report = promote_archive_series(
        _req(tmp_path, files, apply=True, accept_json_decimal=True)
    )
    assert report["status"] == "rejected"
    assert report["failure_reason"] == "ohlcv_mismatch_on_common_timestamps"
    assert files["btc"].read_bytes() == before


def test_duplicate_timestamp_rejects(tmp_path: Path) -> None:
    files = _setup(tmp_path)
    payload = json.loads(files["archive"].read_text(encoding="utf-8"))
    payload["candles"].append(payload["candles"][0])
    files["archive"].write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    report = promote_archive_series(
        _req(tmp_path, files, apply=True, accept_json_decimal=True)
    )
    assert report["status"] == "rejected"


def test_symbol_and_timeframe_mismatch_rejected(tmp_path: Path) -> None:
    files = _setup(tmp_path)
    dump_candles(files["archive"], [_c(0)], symbol="ETHUSDT", timeframe="1h")
    report = promote_archive_series(
        _req(tmp_path, files, apply=True, accept_json_decimal=True)
    )
    assert report["status"] == "rejected"
    assert "symbol mismatch" in report["failure_reason"]
    dump_candles(files["archive"], [_c(0)], symbol="BTCUSDT", timeframe="4h")
    report_tf = promote_archive_series(
        _req(tmp_path, files, apply=True, accept_json_decimal=True)
    )
    assert report_tf["status"] == "rejected"
    assert "timeframe mismatch" in report_tf["failure_reason"]


def test_unaligned_timestamp_rejected(tmp_path: Path) -> None:
    files = _setup(tmp_path)
    dump_candles(
        files["archive"],
        [_c(60_000), _c(HOUR), _c(2 * HOUR)],
        symbol="BTCUSDT",
        timeframe="1h",
    )
    report = promote_archive_series(
        _req(tmp_path, files, apply=True, accept_json_decimal=True)
    )
    assert report["status"] == "rejected"
    assert "not aligned" in report["failure_reason"]


def test_gaps_are_reported_not_filled(tmp_path: Path) -> None:
    universe = _write_universe(tmp_path, ["BTC/USDT"])
    btc = _write_processed(tmp_path, "BTC/USDT", "1h", [_c(2 * HOUR)])
    _write_manifest(tmp_path, universe, {"BTC/USDT": btc})
    archive = tmp_path / "exp" / "candles.json"
    dump_candles(archive, [_c(0)], symbol="BTCUSDT", timeframe="1h")
    report_path = tmp_path / "exp" / "promote_report.json"
    report = promote_archive_series(
        ArchivePromoteRequest(
            root=tmp_path,
            symbol="BTCUSDT",
            timeframe="1h",
            archive_series_path=archive,
            report_path=report_path,
            apply=True,
            accept_json_decimal=True,
        )
    )
    assert report["status"] == "applied"
    stamps = [c.timestamp for c in load_candles(btc)]
    assert stamps == [0, 2 * HOUR]
    assert HOUR not in stamps
    assert report["gaps"] == [HOUR]
    assert "merged_series_has_gaps_not_filled" in report["warnings"]


def test_other_manifest_symbols_preserved(tmp_path: Path) -> None:
    files = _setup(tmp_path)
    eth_before = json.loads(files["manifest"].read_text(encoding="utf-8"))["series"][1]
    promote_archive_series(_req(tmp_path, files, apply=True, accept_json_decimal=True))
    after = json.loads(files["manifest"].read_text(encoding="utf-8"))
    eth_after = next(row for row in after["series"] if row["symbol"] == "ETH/USDT")
    assert eth_after == eth_before
    assert after["universe"]["sha256"] == sha256_file(files["universe"])


def test_new_series_and_manifest_hashes_match(tmp_path: Path) -> None:
    files = _setup(tmp_path)
    promote_archive_series(_req(tmp_path, files, apply=True, accept_json_decimal=True))
    manifest = json.loads(files["manifest"].read_text(encoding="utf-8"))
    btc = next(row for row in manifest["series"] if row["symbol"] == "BTC/USDT")
    assert btc["sha256"] == sha256_file(files["btc"])
    candles = load_candles(files["btc"])
    assert btc["candle_count"] == len(candles)
    assert btc["range_start"] == candles[0].timestamp
    assert btc["range_end"] == candles[-1].timestamp


def test_backtest_loader_accepts_promoted_dataset(tmp_path: Path) -> None:
    files = _setup(tmp_path)
    promote_archive_series(_req(tmp_path, files, apply=True, accept_json_decimal=True))
    dataset = load_backtest_series(
        Path("data/processed/ohlcv/manifest_1h.json"),
        "BTC/USDT",
        root=tmp_path,
    )
    assert dataset.bar_count == 3
    assert dataset.series.candles[0].timestamp == 0


def test_replace_failure_rolls_back_processed(tmp_path: Path) -> None:
    files = _setup(tmp_path)
    before_btc = files["btc"].read_bytes()
    before_man = files["manifest"].read_bytes()
    calls = {"n": 0}

    def flaky(src: Path, dst: Path) -> None:
        calls["n"] += 1
        if dst.name.startswith("manifest"):
            raise OSError("manifest replace failed")
        os.replace(src, dst)

    report = promote_archive_series(
        _req(tmp_path, files, apply=True, accept_json_decimal=True),
        replace_fn=flaky,
    )
    assert report["status"] == "failed"
    assert files["btc"].read_bytes() == before_btc
    assert files["manifest"].read_bytes() == before_man
    journal = tmp_path / "data" / "processed" / "ohlcv" / ".promote_journal"
    assert not journal.exists() or not any(journal.rglob("journal.json"))


def test_rollback_failure_keeps_journal(tmp_path: Path) -> None:
    files = _setup(tmp_path)
    before_btc = files["btc"].read_bytes()
    before_man = files["manifest"].read_bytes()
    calls = {"n": 0}

    def flaky(src: Path, dst: Path) -> None:
        calls["n"] += 1
        if calls["n"] >= 2:
            raise OSError("boom")
        os.replace(src, dst)

    report = promote_archive_series(
        _req(tmp_path, files, apply=True, accept_json_decimal=True),
        replace_fn=flaky,
    )
    assert report["status"] == "failed"
    assert "rollback failed" in (report["failure_reason"] or "")
    assert "promote_journal_present_after_failure" in report["warnings"]
    # Series replace succeeded; series rollback then failed. Manifest never published.
    assert files["btc"].read_bytes() != before_btc
    assert files["manifest"].read_bytes() == before_man
    journal = _journal_json(tmp_path)
    payload = json.loads(journal.read_text(encoding="utf-8"))
    backup_series = journal.parent / "backup_series.json"
    backup_manifest = journal.parent / "backup_manifest.json"
    assert backup_series.is_file()
    assert backup_manifest.is_file()
    assert sha256_file(backup_series) == payload["backup_series_sha256"] == payload["pre_series_sha256"]
    assert sha256_file(backup_manifest) == payload["backup_manifest_sha256"]
    assert sha256_file(files["btc"]) == payload["planned_series_sha256"]
    assert sha256_file(files["manifest"]) == payload["pre_manifest_sha256"]
    resume = _resume(tmp_path, files)
    assert resume["status"] == "rejected"
    assert "series_only" in str(resume["failure_reason"])
    assert resume["recovery_required"] is True
    assert resume["automatic_repair"] is False
    assert files["btc"].read_bytes() != before_btc
    assert files["manifest"].read_bytes() == before_man
    assert journal.is_file()


def test_json_decimal_limitation_not_silent(tmp_path: Path) -> None:
    files = _setup(tmp_path)
    report = promote_archive_series(_req(tmp_path, files, apply=False))
    assert report["apply_blocked_reason"] == "json_decimal_not_original_csv"
    assert report["comparison_method"]["name"] == "json_number_decimal"


def test_report_analysis_is_deterministic(tmp_path: Path) -> None:
    files = _setup(tmp_path)
    first = promote_archive_series(_req(tmp_path, files))
    second = promote_archive_series(_req(tmp_path, files))
    for key in ("counts", "range", "ohlcv_mismatches", "overlap_status", "status"):
        assert first[key] == second[key]


def test_does_not_write_unexpected_processed_paths(tmp_path: Path) -> None:
    files = _setup(tmp_path)
    extra = tmp_path / "data" / "processed" / "ohlcv" / "OTHERUSDT" / "1h.json"
    extra.parent.mkdir(parents=True)
    extra.write_text("LEAVE", encoding="utf-8")
    promote_archive_series(_req(tmp_path, files, apply=True, accept_json_decimal=True))
    assert extra.read_text(encoding="utf-8") == "LEAVE"
    assert files["eth"].read_bytes() != b""
    eth_candles = load_candles(files["eth"])
    assert [c.timestamp for c in eth_candles] == [0, HOUR]


def test_existing_journal_blocks_apply(tmp_path: Path) -> None:
    files = _setup(tmp_path)
    journal = (
        tmp_path / "data" / "processed" / "ohlcv" / ".promote_journal" / "BTCUSDT_1h"
    )
    journal.mkdir(parents=True)
    (journal / "journal.json").write_text("{}", encoding="utf-8")
    before = files["btc"].read_bytes()
    report = promote_archive_series(
        _req(tmp_path, files, apply=True, accept_json_decimal=True)
    )
    assert str(report["failure_reason"]).startswith("incomplete_promote_journal")
    assert files["btc"].read_bytes() == before


def _crash_apply(tmp_path: Path, files: dict[str, Path], crash_at: str) -> subprocess.CompletedProcess[bytes]:
    repo = Path(__file__).resolve().parents[2]
    payload = (
        "from pathlib import Path\n"
        "from tradingbot.data.ohlcv.archive_promote import ArchivePromoteRequest, promote_archive_series\n"
        f"req = ArchivePromoteRequest(root=Path(r'{tmp_path}'), symbol='BTCUSDT', timeframe='1h', "
        f"archive_series_path=Path(r'{files['archive']}'), report_path=Path(r'{files['report']}'), "
        "apply=True, accept_json_decimal=True)\n"
        f"promote_archive_series(req, crash_at={crash_at!r})\n"
    )
    env = os.environ.copy()
    env["PYTHONPATH"] = str(repo / "src") + os.pathsep + env.get("PYTHONPATH", "")
    return subprocess.run(
        [sys.executable, "-c", payload],
        cwd=str(repo),
        env=env,
        capture_output=True,
        check=False,
    )


def _resume(tmp_path: Path, files: dict[str, Path]):
    return promote_archive_series(
        _req(tmp_path, files, apply=True, accept_json_decimal=True)
    )


def test_crash_after_backed_up_blocks_and_keeps_originals(tmp_path: Path) -> None:
    files = _setup(tmp_path)
    before_btc = files["btc"].read_bytes()
    before_man = files["manifest"].read_bytes()
    crashed = _crash_apply(tmp_path, files, "after_backed_up")
    assert crashed.returncode == 91
    assert files["btc"].read_bytes() == before_btc
    assert files["manifest"].read_bytes() == before_man
    report = _resume(tmp_path, files)
    assert report["status"] == "rejected"
    assert "stale_unpublished" in str(report["failure_reason"])
    assert report["recovery_required"] is True
    assert report["automatic_repair"] is False
    assert files["btc"].read_bytes() == before_btc
    assert files["manifest"].read_bytes() == before_man
    backup = next(_journal_root(tmp_path).rglob("backup_series.json"))
    assert backup.is_file()
    assert _journal_json(tmp_path).is_file()


def test_crash_after_series_replace_before_journal_state(tmp_path: Path) -> None:
    files = _setup(tmp_path)
    before_btc = files["btc"].read_bytes()
    before_man = files["manifest"].read_bytes()
    crashed = _crash_apply(tmp_path, files, "after_series_replace")
    assert crashed.returncode == 91
    assert files["btc"].read_bytes() != before_btc
    assert files["manifest"].read_bytes() == before_man
    after_crash_btc = files["btc"].read_bytes()
    journal_before = _journal_json(tmp_path).read_bytes()
    report = _resume(tmp_path, files)
    assert report["status"] == "rejected"
    assert "series_only" in str(report["failure_reason"])
    assert "manual recovery required" in str(report["failure_reason"])
    assert "no automatic repair" in str(report["failure_reason"])
    assert report["files_changed"] is True
    assert report["recovery_required"] is True
    assert report["automatic_repair"] is False
    assert report["live_files"]["series"] == "planned"
    assert report["live_files"]["manifest"] == "pre_promote"
    assert files["btc"].read_bytes() == after_crash_btc
    assert files["manifest"].read_bytes() == before_man
    assert _journal_json(tmp_path).read_bytes() == journal_before


def test_crash_after_series_published_journal(tmp_path: Path) -> None:
    files = _setup(tmp_path)
    before_man = files["manifest"].read_bytes()
    crashed = _crash_apply(tmp_path, files, "after_series_published")
    assert crashed.returncode == 91
    after_crash_btc = files["btc"].read_bytes()
    report = _resume(tmp_path, files)
    assert report["status"] == "rejected"
    assert "series_only" in str(report["failure_reason"])
    assert report["recovery_required"] is True
    assert report["automatic_repair"] is False
    assert files["btc"].read_bytes() == after_crash_btc
    assert files["manifest"].read_bytes() == before_man
    journal = json.loads(_journal_json(tmp_path).read_text(encoding="utf-8"))
    assert journal["state"] == "series_published"
    assert sha256_file(files["btc"]) == journal["planned_series_sha256"]
    assert sha256_file(files["manifest"]) == journal["pre_manifest_sha256"]


def test_crash_after_manifest_replace_before_committed(tmp_path: Path) -> None:
    files = _setup(tmp_path)
    eth_before = files["eth"].read_bytes()
    universe_before = sha256_file(files["universe"])
    crashed = _crash_apply(tmp_path, files, "after_manifest_replace")
    assert crashed.returncode == 91
    journal = json.loads(_journal_json(tmp_path).read_text(encoding="utf-8"))
    planned_series = journal["planned_series_sha256"]
    planned_manifest = journal["planned_manifest_sha256"]
    assert sha256_file(files["btc"]) == planned_series
    assert sha256_file(files["manifest"]) == planned_manifest
    report = _resume(tmp_path, files)
    assert report["status"] == "recovered_complete"
    assert report["files_changed"] is False
    assert sha256_file(files["btc"]) == planned_series
    assert sha256_file(files["manifest"]) == planned_manifest
    assert report["after"]["processed_sha256"] == planned_series
    assert report["after"]["manifest_sha256"] == planned_manifest
    candles = load_candles(files["btc"])
    assert [c.timestamp for c in candles] == [0, HOUR, 2 * HOUR]
    manifest = json.loads(files["manifest"].read_text(encoding="utf-8"))
    btc = next(row for row in manifest["series"] if row["symbol"] == "BTC/USDT")
    eth = next(row for row in manifest["series"] if row["symbol"] == "ETH/USDT")
    assert btc["sha256"] == sha256_file(files["btc"])
    assert files["eth"].read_bytes() == eth_before
    assert eth["sha256"] == sha256_file(files["eth"])
    assert manifest["universe"]["sha256"] == universe_before
    assert not any(_journal_root(tmp_path).rglob("journal.json"))


def test_crash_after_committed_before_journal_delete(tmp_path: Path) -> None:
    files = _setup(tmp_path)
    eth_before = files["eth"].read_bytes()
    crashed = _crash_apply(tmp_path, files, "after_committed")
    assert crashed.returncode == 91
    journal = json.loads(_journal_json(tmp_path).read_text(encoding="utf-8"))
    planned_series = journal["planned_series_sha256"]
    planned_manifest = journal["planned_manifest_sha256"]
    report = _resume(tmp_path, files)
    assert report["status"] == "recovered_complete"
    assert sha256_file(files["btc"]) == planned_series
    assert sha256_file(files["manifest"]) == planned_manifest
    assert files["eth"].read_bytes() == eth_before
    dataset = load_backtest_series(
        Path("data/processed/ohlcv/manifest_1h.json"),
        "BTC/USDT",
        root=tmp_path,
    )
    assert dataset.bar_count == 3
    assert not any(_journal_root(tmp_path).rglob("journal.json"))


def test_loader_reject_rolls_back_bytes(tmp_path: Path) -> None:
    files = _setup(tmp_path)
    before_btc = files["btc"].read_bytes()
    before_man = files["manifest"].read_bytes()

    def boom_loader(*_args, **_kwargs):
        raise ValueError("loader reject")

    report = promote_archive_series(
        _req(tmp_path, files, apply=True, accept_json_decimal=True),
        loader_fn=boom_loader,
    )
    assert report["status"] == "failed"
    assert files["btc"].read_bytes() == before_btc
    assert files["manifest"].read_bytes() == before_man
    assert not any(
        (tmp_path / "data" / "processed" / "ohlcv" / ".promote_journal").rglob("journal.json")
    )


def test_missing_backup_aborts_rollback(tmp_path: Path) -> None:
    files = _setup(tmp_path)
    before_btc = files["btc"].read_bytes()
    before_man = files["manifest"].read_bytes()

    def flaky(src: Path, dst: Path) -> None:
        if dst.name.startswith("manifest"):
            for backup in _journal_root(tmp_path).rglob("backup_series.json"):
                backup.unlink()
            raise OSError("manifest replace failed")
        os.replace(src, dst)

    report = promote_archive_series(
        _req(tmp_path, files, apply=True, accept_json_decimal=True),
        replace_fn=flaky,
    )
    assert report["status"] == "failed"
    assert "backup_series missing" in (report["failure_reason"] or "")
    assert "promote_journal_present_after_failure" in report["warnings"]
    # Series published; backup gone so rollback aborted. Manifest still original.
    assert files["btc"].read_bytes() != before_btc
    assert files["manifest"].read_bytes() == before_man
    journal = _journal_json(tmp_path)
    payload = json.loads(journal.read_text(encoding="utf-8"))
    assert not (journal.parent / "backup_series.json").is_file()
    assert (journal.parent / "backup_manifest.json").is_file()
    assert sha256_file(files["btc"]) == payload["planned_series_sha256"]
    resume = _resume(tmp_path, files)
    assert resume["status"] == "rejected"
    assert "backup_invalid" in str(resume["failure_reason"])
    assert "backup_series_missing" in resume["journal_inspection"]["failed_checks"]
    assert files["btc"].read_bytes() != before_btc
    assert files["manifest"].read_bytes() == before_man
    assert journal.is_file()


def test_wrong_backup_hash_aborts_rollback(tmp_path: Path) -> None:
    files = _setup(tmp_path)
    before_btc = files["btc"].read_bytes()
    before_man = files["manifest"].read_bytes()

    def flaky(src: Path, dst: Path) -> None:
        if dst.name.startswith("manifest"):
            for backup in _journal_root(tmp_path).rglob("backup_series.json"):
                backup.write_bytes(b"tampered")
            raise OSError("manifest replace failed")
        os.replace(src, dst)

    report = promote_archive_series(
        _req(tmp_path, files, apply=True, accept_json_decimal=True),
        replace_fn=flaky,
    )
    assert report["status"] == "failed"
    assert "backup_series hash mismatch" in (report["failure_reason"] or "")
    assert files["btc"].read_bytes() != before_btc
    assert files["manifest"].read_bytes() == before_man
    backup = next(_journal_root(tmp_path).rglob("backup_series.json"))
    payload = json.loads(_journal_json(tmp_path).read_text(encoding="utf-8"))
    assert sha256_file(backup) != payload["backup_series_sha256"]
    assert sha256_file(files["btc"]) == payload["planned_series_sha256"]
    resume = _resume(tmp_path, files)
    assert resume["status"] == "rejected"
    assert "backup_invalid" in str(resume["failure_reason"])
    assert "backup_series_hash" in resume["journal_inspection"]["failed_checks"]
    assert files["btc"].read_bytes() != before_btc
    assert files["manifest"].read_bytes() == before_man
    assert _journal_json(tmp_path).is_file()


def test_manifest_rollback_failure_keeps_journal(tmp_path: Path) -> None:
    files = _setup(tmp_path)
    before_btc = files["btc"].read_bytes()
    before_man = files["manifest"].read_bytes()

    def flaky(src: Path, dst: Path) -> None:
        if src.name.startswith("backup_manifest"):
            raise OSError("manifest rollback failed")
        os.replace(src, dst)

    def boom_loader(*_args, **_kwargs):
        raise ValueError("loader reject")

    report = promote_archive_series(
        _req(tmp_path, files, apply=True, accept_json_decimal=True),
        replace_fn=flaky,
        loader_fn=boom_loader,
    )
    assert report["status"] == "failed"
    assert "rollback failed" in (report["failure_reason"] or "") or "manifest rollback" in (
        report["failure_reason"] or ""
    )
    # Series rolled back (os.replace consumes backup_series). Manifest rollback
    # failed, so live files are mixed: original series + new manifest.
    assert files["btc"].read_bytes() == before_btc
    assert files["manifest"].read_bytes() != before_man
    journal = _journal_json(tmp_path)
    payload = json.loads(journal.read_text(encoding="utf-8"))
    assert sha256_file(files["btc"]) == payload["pre_series_sha256"]
    assert sha256_file(files["manifest"]) == payload["planned_manifest_sha256"]
    assert not (journal.parent / "backup_series.json").is_file()
    assert (journal.parent / "backup_manifest.json").is_file()
    resume = _resume(tmp_path, files)
    assert resume["status"] == "rejected"
    assert "backup_invalid" in str(resume["failure_reason"])
    assert "backup_series_missing" in resume["journal_inspection"]["failed_checks"]
    assert resume["recovery_required"] is True
    assert resume["automatic_repair"] is False
    assert files["btc"].read_bytes() == before_btc
    assert files["manifest"].read_bytes() != before_man
    assert journal.is_file()


def _crash_backed_up(tmp_path: Path, files: dict[str, Path]) -> dict:
    crashed = _crash_apply(tmp_path, files, "after_backed_up")
    assert crashed.returncode == 91
    return json.loads(_journal_json(tmp_path).read_text(encoding="utf-8"))


def _assert_journal_hash_reject(
    tmp_path: Path,
    files: dict[str, Path],
    *,
    before_btc: bytes,
    before_man: bytes,
    failed_token: str,
) -> None:
    journal_bytes = _journal_json(tmp_path).read_bytes()
    report = _resume(tmp_path, files)
    assert report["status"] == "rejected"
    assert report["recovery_required"] is True
    assert report["automatic_repair"] is False
    assert failed_token in report["journal_inspection"]["failed_checks"]
    assert files["btc"].read_bytes() == before_btc
    assert files["manifest"].read_bytes() == before_man
    assert _journal_json(tmp_path).read_bytes() == journal_bytes
    again = _resume(tmp_path, files)
    assert again["status"] == "rejected"
    assert files["btc"].read_bytes() == before_btc


def test_resume_rejects_missing_backup_series_hash(tmp_path: Path) -> None:
    files = _setup(tmp_path)
    before_btc = files["btc"].read_bytes()
    before_man = files["manifest"].read_bytes()
    payload = _crash_backed_up(tmp_path, files)
    del payload["backup_series_sha256"]
    _journal_json(tmp_path).write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    _assert_journal_hash_reject(
        tmp_path,
        files,
        before_btc=before_btc,
        before_man=before_man,
        failed_token="backup_series_sha256_missing",
    )


def test_resume_rejects_missing_backup_manifest_hash(tmp_path: Path) -> None:
    files = _setup(tmp_path)
    before_btc = files["btc"].read_bytes()
    before_man = files["manifest"].read_bytes()
    payload = _crash_backed_up(tmp_path, files)
    del payload["backup_manifest_sha256"]
    _journal_json(tmp_path).write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    _assert_journal_hash_reject(
        tmp_path,
        files,
        before_btc=before_btc,
        before_man=before_man,
        failed_token="backup_manifest_sha256_missing",
    )


def test_resume_rejects_empty_backup_hash(tmp_path: Path) -> None:
    files = _setup(tmp_path)
    before_btc = files["btc"].read_bytes()
    before_man = files["manifest"].read_bytes()
    payload = _crash_backed_up(tmp_path, files)
    payload["backup_series_sha256"] = ""
    _journal_json(tmp_path).write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    _assert_journal_hash_reject(
        tmp_path,
        files,
        before_btc=before_btc,
        before_man=before_man,
        failed_token="backup_series_sha256_missing",
    )


def test_resume_rejects_invalid_backup_hash_format(tmp_path: Path) -> None:
    files = _setup(tmp_path)
    before_btc = files["btc"].read_bytes()
    before_man = files["manifest"].read_bytes()
    payload = _crash_backed_up(tmp_path, files)
    payload["backup_series_sha256"] = "not-a-sha256"
    _journal_json(tmp_path).write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    _assert_journal_hash_reject(
        tmp_path,
        files,
        before_btc=before_btc,
        before_man=before_man,
        failed_token="backup_series_sha256_invalid",
    )


def test_resume_rejects_mismatched_backup_file_hash(tmp_path: Path) -> None:
    files = _setup(tmp_path)
    before_btc = files["btc"].read_bytes()
    before_man = files["manifest"].read_bytes()
    _crash_backed_up(tmp_path, files)
    backup = next(_journal_root(tmp_path).rglob("backup_series.json"))
    backup.write_bytes(b"tampered-backup")
    _assert_journal_hash_reject(
        tmp_path,
        files,
        before_btc=before_btc,
        before_man=before_man,
        failed_token="backup_series_hash",
    )


def test_complete_journal_loader_reject_keeps_journal(tmp_path: Path) -> None:
    files = _setup(tmp_path)
    eth_before = files["eth"].read_bytes()
    crashed = _crash_apply(tmp_path, files, "after_committed")
    assert crashed.returncode == 91
    journal = json.loads(_journal_json(tmp_path).read_text(encoding="utf-8"))
    series_sha = sha256_file(files["btc"])
    manifest_sha = sha256_file(files["manifest"])
    assert series_sha == journal["planned_series_sha256"]
    assert manifest_sha == journal["planned_manifest_sha256"]

    def boom_loader(*_args, **_kwargs):
        raise ValueError("loader reject")

    report = promote_archive_series(
        _req(tmp_path, files, apply=True, accept_json_decimal=True),
        loader_fn=boom_loader,
    )
    assert report["status"] == "rejected"
    assert str(report["failure_reason"]).startswith("recovery_loader_failed")
    assert report["files_changed"] is False
    assert report["recovery_required"] is True
    assert sha256_file(files["btc"]) == series_sha
    assert sha256_file(files["manifest"]) == manifest_sha
    assert files["eth"].read_bytes() == eth_before
    assert _journal_json(tmp_path).is_file()
    again = _resume(tmp_path, files)
    assert again["status"] == "recovered_complete"
    assert sha256_file(files["btc"]) == series_sha
    assert not any(_journal_root(tmp_path).rglob("journal.json"))


def test_cli_dry_run_exits_zero(tmp_path: Path) -> None:
    files = _setup(tmp_path)
    result = _run_cli(tmp_path, files, [])
    assert result.returncode == 0
    assert b"status=dry_run" in result.stdout


def test_cli_rejected_journal_exits_nonzero(tmp_path: Path) -> None:
    files = _setup(tmp_path)
    journal = _journal_root(tmp_path) / "BTCUSDT_1h"
    journal.mkdir(parents=True)
    (journal / "journal.json").write_text("{}", encoding="utf-8")
    result = _run_cli(tmp_path, files, ["--apply", "--accept-json-decimal"])
    assert result.returncode == 1
    assert b"status=rejected" in result.stdout
    assert b"incomplete_promote_journal" in result.stdout


def test_cli_recovered_complete_exits_zero(tmp_path: Path) -> None:
    files = _setup(tmp_path)
    crashed = _crash_apply(tmp_path, files, "after_committed")
    assert crashed.returncode == 91
    result = _run_cli(tmp_path, files, ["--apply", "--accept-json-decimal"])
    assert result.returncode == 0
    assert b"status=recovered_complete" in result.stdout
    assert not any(_journal_root(tmp_path).rglob("journal.json"))


def test_cli_failed_apply_exits_nonzero(tmp_path: Path) -> None:
    files = _setup(tmp_path)
    dump_candles(
        files["archive"],
        [_c(0), _c(HOUR, close=9.0), _c(2 * HOUR)],
        symbol="BTCUSDT",
        timeframe="1h",
    )
    result = _run_cli(tmp_path, files, ["--apply", "--accept-json-decimal"])
    assert result.returncode == 1
    assert b"status=rejected" in result.stdout

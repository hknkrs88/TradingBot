from __future__ import annotations

import json
import os
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
    journal = tmp_path / "data" / "processed" / "ohlcv" / ".promote_journal"
    assert any(journal.rglob("journal.json"))


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
    assert report["failure_reason"] == "incomplete_promote_journal"
    assert files["btc"].read_bytes() == before

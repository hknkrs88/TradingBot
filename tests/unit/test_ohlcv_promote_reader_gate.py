from __future__ import annotations

from pathlib import Path

import pytest

from tradingbot.backtest.dataset import load_backtest_series
from tradingbot.data.io import InvalidCacheError, atomic_write_json, sha256_file
from tradingbot.data.ohlcv.archive_promote import (
    JOURNAL_SCHEMA,
    inspect_promote_journal,
    promote_journal_dir,
    refuse_unsafe_promote_journal,
)
from tradingbot.data.ohlcv.models import Candle
from tradingbot.data.ohlcv.reader import load_processed
from tradingbot.data.ohlcv.store import dump_candles, load_candles

HOUR = 3_600_000
SCHEMA = "ohlcv_dataset_manifest/v1"
MANIFEST_REL = Path("data/processed/ohlcv/manifest_1h.json")
PLANNED_HEX = "ab" * 32


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
            "symbol_count": len(series_files),
            "symbols": list(series_files),
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
    return {"universe": universe, "btc": btc, "eth": eth, "manifest": manifest}


def _bytes(files: dict[str, Path]) -> dict[str, bytes]:
    root = files["manifest"].parents[3]
    journal_dir = promote_journal_dir(root, "BTC/USDT", "1h")
    snapshot = {key: path.read_bytes() for key, path in files.items() if path.is_file()}
    if journal_dir.exists():
        for path in journal_dir.rglob("*"):
            if path.is_file():
                snapshot[f"journal:{path.name}"] = path.read_bytes()
    return snapshot


def _root(files: dict[str, Path]) -> Path:
    return files["manifest"].parents[3]


def _install_journal(
    files: dict[str, Path],
    *,
    pre_series: str,
    pre_manifest: str,
    planned_series: str,
    planned_manifest: str,
    backup_series: bytes,
    backup_manifest: bytes,
) -> Path:
    root = _root(files)
    journal_dir = promote_journal_dir(root, "BTC/USDT", "1h")
    journal_dir.mkdir(parents=True, exist_ok=True)
    (journal_dir / "backup_series.json").write_bytes(backup_series)
    (journal_dir / "backup_manifest.json").write_bytes(backup_manifest)
    atomic_write_json(
        journal_dir / "journal.json",
        {
            "schema": JOURNAL_SCHEMA,
            "state": "test",
            "pre_series_sha256": pre_series,
            "pre_manifest_sha256": pre_manifest,
            "backup_series_sha256": sha256_file(journal_dir / "backup_series.json"),
            "backup_manifest_sha256": sha256_file(journal_dir / "backup_manifest.json"),
            "planned_series_sha256": planned_series,
            "planned_manifest_sha256": planned_manifest,
        },
    )
    return journal_dir


def _assert_both_refuse(tmp_path: Path, *, kind: str) -> None:
    with pytest.raises(InvalidCacheError, match=rf"unsafe_promote_journal:{kind}:"):
        load_processed("BTC/USDT", "1h", root=tmp_path)
    with pytest.raises(InvalidCacheError, match=rf"unsafe_promote_journal:{kind}:"):
        load_backtest_series(MANIFEST_REL, "BTC/USDT", root=tmp_path)


def test_no_journal_readers_unchanged(tmp_path: Path) -> None:
    files = _setup(tmp_path)
    before = files["btc"].read_bytes()
    candles = load_processed("BTC/USDT", "1h", root=tmp_path)
    assert [item.timestamp for item in candles] == [HOUR, 2 * HOUR]
    dataset = load_backtest_series(MANIFEST_REL, "BTC/USDT", root=tmp_path)
    assert dataset.bar_count == 2
    assert dataset.processed_sha256 == sha256_file(files["btc"])
    assert inspect_promote_journal(tmp_path, "BTC/USDT", "1h")["kind"] == "none"
    assert files["btc"].read_bytes() == before


def test_series_only_rejects_both_readers(tmp_path: Path) -> None:
    files = _setup(tmp_path)
    pre_series = sha256_file(files["btc"])
    pre_manifest = sha256_file(files["manifest"])
    backup_series = files["btc"].read_bytes()
    backup_manifest = files["manifest"].read_bytes()
    dump_candles(
        files["btc"],
        [_c(0), _c(HOUR), _c(2 * HOUR)],
        symbol="BTC/USDT",
        timeframe="1h",
    )
    planned_series = sha256_file(files["btc"])
    _install_journal(
        files,
        pre_series=pre_series,
        pre_manifest=pre_manifest,
        planned_series=planned_series,
        planned_manifest=PLANNED_HEX,
        backup_series=backup_series,
        backup_manifest=backup_manifest,
    )
    assert inspect_promote_journal(tmp_path, "BTC/USDT", "1h")["kind"] == "series_only"
    before = _bytes(files)
    _assert_both_refuse(tmp_path, kind="series_only")
    assert _bytes(files) == before


def test_ambiguous_rejects_both_readers(tmp_path: Path) -> None:
    files = _setup(tmp_path)
    pre_series = sha256_file(files["btc"])
    pre_manifest = sha256_file(files["manifest"])
    backup_series = files["btc"].read_bytes()
    backup_manifest = files["manifest"].read_bytes()
    files["btc"].write_bytes(backup_series + b"\n")
    _install_journal(
        files,
        pre_series=pre_series,
        pre_manifest=pre_manifest,
        planned_series=PLANNED_HEX,
        planned_manifest="cd" * 32,
        backup_series=backup_series,
        backup_manifest=backup_manifest,
    )
    assert inspect_promote_journal(tmp_path, "BTC/USDT", "1h")["kind"] == "ambiguous"
    before = _bytes(files)
    _assert_both_refuse(tmp_path, kind="ambiguous")
    assert _bytes(files) == before


def test_backup_invalid_rejects_both_readers(tmp_path: Path) -> None:
    files = _setup(tmp_path)
    pre_series = sha256_file(files["btc"])
    pre_manifest = sha256_file(files["manifest"])
    journal_dir = _install_journal(
        files,
        pre_series=pre_series,
        pre_manifest=pre_manifest,
        planned_series=PLANNED_HEX,
        planned_manifest="cd" * 32,
        backup_series=files["btc"].read_bytes(),
        backup_manifest=files["manifest"].read_bytes(),
    )
    (journal_dir / "backup_series.json").write_bytes(b"tampered-backup")
    assert inspect_promote_journal(tmp_path, "BTC/USDT", "1h")["kind"] == "backup_invalid"
    before = _bytes(files)
    _assert_both_refuse(tmp_path, kind="backup_invalid")
    assert _bytes(files) == before


def test_stale_unpublished_allows_consistent_pre_state(tmp_path: Path) -> None:
    files = _setup(tmp_path)
    live_series = files["btc"].read_bytes()
    live_manifest = files["manifest"].read_bytes()
    pre_series = sha256_file(files["btc"])
    pre_manifest = sha256_file(files["manifest"])
    _install_journal(
        files,
        pre_series=pre_series,
        pre_manifest=pre_manifest,
        planned_series=PLANNED_HEX,
        planned_manifest="cd" * 32,
        backup_series=live_series,
        backup_manifest=live_manifest,
    )
    assert inspect_promote_journal(tmp_path, "BTC/USDT", "1h")["kind"] == "stale_unpublished"
    candles = load_processed("BTC/USDT", "1h", root=tmp_path)
    assert [item.timestamp for item in candles] == [HOUR, 2 * HOUR]
    dataset = load_backtest_series(MANIFEST_REL, "BTC/USDT", root=tmp_path)
    assert dataset.processed_sha256 == pre_series
    assert files["btc"].read_bytes() == live_series
    assert files["manifest"].read_bytes() == live_manifest
    assert (promote_journal_dir(tmp_path, "BTC/USDT", "1h") / "journal.json").is_file()


def test_complete_allows_planned_state_without_deleting_journal(tmp_path: Path) -> None:
    files = _setup(tmp_path)
    backup_series = files["btc"].read_bytes()
    backup_manifest = files["manifest"].read_bytes()
    pre_series = sha256_file(files["btc"])
    pre_manifest = sha256_file(files["manifest"])
    dump_candles(
        files["btc"],
        [_c(0), _c(HOUR), _c(2 * HOUR)],
        symbol="BTC/USDT",
        timeframe="1h",
    )
    universe = _write_universe(tmp_path, ["BTC/USDT", "ETH/USDT"])
    files["manifest"] = _write_manifest(
        tmp_path, universe, {"BTC/USDT": files["btc"], "ETH/USDT": files["eth"]}
    )
    planned_series = sha256_file(files["btc"])
    planned_manifest = sha256_file(files["manifest"])
    journal_dir = _install_journal(
        files,
        pre_series=pre_series,
        pre_manifest=pre_manifest,
        planned_series=planned_series,
        planned_manifest=planned_manifest,
        backup_series=backup_series,
        backup_manifest=backup_manifest,
    )
    assert inspect_promote_journal(tmp_path, "BTC/USDT", "1h")["kind"] == "complete"
    candles = load_processed("BTC/USDT", "1h", root=tmp_path)
    assert [item.timestamp for item in candles] == [0, HOUR, 2 * HOUR]
    dataset = load_backtest_series(MANIFEST_REL, "BTC/USDT", root=tmp_path)
    assert dataset.bar_count == 3
    assert (journal_dir / "journal.json").is_file()
    assert (journal_dir / "backup_series.json").read_bytes() == backup_series


def test_series_only_after_series_replace_shape_is_unreadable(tmp_path: Path) -> None:
    files = _setup(tmp_path)
    pre_series = sha256_file(files["btc"])
    pre_manifest = sha256_file(files["manifest"])
    backup_series = files["btc"].read_bytes()
    backup_manifest = files["manifest"].read_bytes()
    dump_candles(
        files["btc"],
        [_c(0), _c(HOUR), _c(2 * HOUR)],
        symbol="BTC/USDT",
        timeframe="1h",
    )
    _install_journal(
        files,
        pre_series=pre_series,
        pre_manifest=pre_manifest,
        planned_series=sha256_file(files["btc"]),
        planned_manifest=PLANNED_HEX,
        backup_series=backup_series,
        backup_manifest=backup_manifest,
    )
    assert inspect_promote_journal(tmp_path, "BTC/USDT", "1h")["kind"] == "series_only"
    before = _bytes(files)
    with pytest.raises(InvalidCacheError, match="series_only"):
        load_processed("BTCUSDT", "1h", root=tmp_path)
    with pytest.raises(InvalidCacheError, match="series_only"):
        load_backtest_series(MANIFEST_REL, "BTC/USDT", root=tmp_path)
    assert _bytes(files) == before


def test_backtest_hash_mismatch_still_applies_without_journal(tmp_path: Path) -> None:
    files = _setup(tmp_path)
    dump_candles(
        files["btc"],
        [_c(0), _c(HOUR), _c(2 * HOUR)],
        symbol="BTC/USDT",
        timeframe="1h",
    )
    with pytest.raises(ValueError, match="processed hash mismatch"):
        load_backtest_series(MANIFEST_REL, "BTC/USDT", root=tmp_path)
    candles = load_processed("BTC/USDT", "1h", root=tmp_path)
    assert len(candles) == 3


def test_unknown_kind_is_fail_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    files = _setup(tmp_path)
    monkeypatch.setattr(
        "tradingbot.data.ohlcv.archive_promote.inspect_promote_journal",
        lambda root, symbol, timeframe: {"kind": "unexpected", "detail": "injected"},
    )
    with pytest.raises(InvalidCacheError, match="unsafe_promote_journal:unexpected:injected"):
        refuse_unsafe_promote_journal(tmp_path, "BTC/USDT", "1h")
    with pytest.raises(InvalidCacheError, match="unsafe_promote_journal:unexpected:"):
        load_processed("BTC/USDT", "1h", root=tmp_path)
    assert files["btc"].is_file()
    assert files["manifest"].is_file()

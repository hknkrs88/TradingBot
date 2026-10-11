from pathlib import Path

import pytest

from tradingbot.data.io import InvalidCacheError, atomic_write_json, sha256_file
from tradingbot.data.ohlcv.archive_promote import (
    JOURNAL_SCHEMA,
    inspect_promote_journal,
    promote_journal_dir,
)
from tradingbot.data.ohlcv.models import Candle
from tradingbot.data.ohlcv.processed_sufficiency import assess_processed_4h
from tradingbot.data.ohlcv.store import dump_candles, processed_path
from tradingbot.data.ohlcv.sufficiency import TIMEFRAME
from tradingbot.data.ohlcv.universe_sufficiency import assess_universe_4h

TF = 14_400_000
BTC = "BTC/USDT"
ETH = "ETH/USDT"


def _c(index: int, **over: float) -> Candle:
    row = {"open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5, "volume": 10.0, **over}
    return Candle(timestamp=index * TF, **row)


def _series(count: int, *, start: int = 0) -> list[Candle]:
    return [_c(start + index) for index in range(count)]


def _universe(root: Path, rows: list[dict]) -> Path:
    path = root / "data" / "processed" / "universe.json"
    atomic_write_json(path, {"universe": rows})
    return path


def _symbols_universe(root: Path, symbols: list[str]) -> Path:
    return _universe(root, [{"binance_symbol": item} for item in symbols])


def _processed(root: Path, symbol: str, candles: list[Candle], *, timeframe: str = TIMEFRAME) -> Path:
    path = processed_path(root / "data" / "processed" / "ohlcv", symbol, TIMEFRAME)
    dump_candles(path, candles, symbol=symbol, timeframe=timeframe)
    return path


def _scan(root: Path, universe_path: Path, *, now_ms: int):
    return assess_universe_4h(universe_path=universe_path, root=root, now_ms=now_ms)


def test_two_eligible_symbols_keep_universe_order(tmp_path: Path) -> None:
    universe = _symbols_universe(tmp_path, [ETH, BTC])
    _processed(tmp_path, ETH, _series(50))
    _processed(tmp_path, BTC, _series(50))
    report = _scan(tmp_path, universe, now_ms=50 * TF)
    assert [item.symbol for item in report.assessments] == [ETH, BTC]
    assert [item.symbol for item in report.eligible] == [ETH, BTC]
    assert report.rejected == ()
    assert all(item.sufficiency.eligible is True for item in report.eligible)


def test_one_eligible_one_too_few_closed(tmp_path: Path) -> None:
    universe = _symbols_universe(tmp_path, [BTC, ETH])
    _processed(tmp_path, BTC, _series(50))
    _processed(tmp_path, ETH, _series(49))
    report = _scan(tmp_path, universe, now_ms=50 * TF)
    assert report.eligible[0].symbol == BTC
    assert [item.symbol for item in report.rejected] == [ETH]
    assert report.rejected[0].sufficiency.reason == "too_few_closed_candles"


def test_missing_cache_does_not_stop_other_symbols(tmp_path: Path) -> None:
    universe = _symbols_universe(tmp_path, [BTC, ETH])
    _processed(tmp_path, BTC, _series(50))
    report = _scan(tmp_path, universe, now_ms=50 * TF)
    by_symbol = {item.symbol: item.sufficiency for item in report.assessments}
    assert by_symbol[BTC].eligible is True
    assert by_symbol[ETH].eligible is False
    assert by_symbol[ETH].reason == "missing_processed_cache"


def test_corrupt_cache_does_not_stop_other_symbols(tmp_path: Path) -> None:
    universe = _symbols_universe(tmp_path, [BTC, ETH])
    path = processed_path(tmp_path / "data" / "processed" / "ohlcv", BTC, TIMEFRAME)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not-json", encoding="utf-8")
    _processed(tmp_path, ETH, _series(50))
    report = _scan(tmp_path, universe, now_ms=50 * TF)
    by_symbol = {item.symbol: item.sufficiency for item in report.assessments}
    assert by_symbol[BTC].eligible is False
    assert by_symbol[BTC].reason == "corrupt_processed_cache"
    assert by_symbol[ETH].eligible is True


def test_missing_candles_list_does_not_stop_other_symbols(tmp_path: Path) -> None:
    universe = _symbols_universe(tmp_path, [BTC, ETH])
    path = processed_path(tmp_path / "data" / "processed" / "ohlcv", BTC, TIMEFRAME)
    atomic_write_json(path, {"symbol": BTC, "timeframe": TIMEFRAME})
    _processed(tmp_path, ETH, _series(50))
    report = _scan(tmp_path, universe, now_ms=50 * TF)
    by_symbol = {item.symbol: item.sufficiency for item in report.assessments}
    assert by_symbol[BTC].eligible is False
    assert by_symbol[BTC].reason == "invalid_processed_payload"
    assert by_symbol[ETH].eligible is True


def test_invalid_processed_ohlcv_does_not_stop_other_symbols(tmp_path: Path) -> None:
    universe = _symbols_universe(tmp_path, [BTC, ETH])
    path = processed_path(tmp_path / "data" / "processed" / "ohlcv", BTC, TIMEFRAME)
    atomic_write_json(path, [])
    _processed(tmp_path, ETH, _series(50))
    report = _scan(tmp_path, universe, now_ms=50 * TF)
    by_symbol = {item.symbol: item.sufficiency for item in report.assessments}
    assert by_symbol[BTC].eligible is False
    assert by_symbol[BTC].reason == "invalid_processed_payload"
    assert by_symbol[ETH].eligible is True


def test_source_identity_mismatch_rejects_only_that_symbol(tmp_path: Path) -> None:
    universe = _symbols_universe(tmp_path, [BTC, ETH])
    _processed(tmp_path, BTC, _series(50))
    path = processed_path(tmp_path / "data" / "processed" / "ohlcv", ETH, TIMEFRAME)
    dump_candles(path, _series(50), symbol=BTC, timeframe=TIMEFRAME)
    report = _scan(tmp_path, universe, now_ms=50 * TF)
    by_symbol = {item.symbol: item.sufficiency for item in report.assessments}
    assert by_symbol[BTC].eligible is True
    assert by_symbol[ETH].eligible is False
    assert by_symbol[ETH].reason == "symbol_mismatch"


def test_gaps_do_not_reject_by_themselves(tmp_path: Path) -> None:
    universe = _symbols_universe(tmp_path, [BTC])
    _processed(tmp_path, BTC, _series(49) + [_c(50)])
    report = _scan(tmp_path, universe, now_ms=51 * TF)
    result = report.assessments[0].sufficiency
    assert result.eligible is True
    assert result.has_gaps is True
    assert result.reason is None
    assert report.rejected == ()


def test_not_caught_up_does_not_reject_by_itself(tmp_path: Path) -> None:
    universe = _symbols_universe(tmp_path, [BTC])
    _processed(tmp_path, BTC, _series(50))
    report = _scan(tmp_path, universe, now_ms=51 * TF)
    result = report.assessments[0].sufficiency
    assert result.caught_up is False
    assert result.eligible is True
    assert result.reason is None


def test_series_only_journal_rejects_only_that_symbol(tmp_path: Path) -> None:
    universe = _symbols_universe(tmp_path, [BTC, ETH])
    series_path = _processed(tmp_path, BTC, _series(50))
    _processed(tmp_path, ETH, _series(50))
    manifest_path = tmp_path / "data" / "processed" / "ohlcv" / "manifest_4h.json"
    atomic_write_json(manifest_path, {"schema": "ohlcv_dataset_manifest/v1", "timeframe": "4h"})
    pre_series = sha256_file(series_path)
    pre_manifest = sha256_file(manifest_path)
    backup_series = series_path.read_bytes()
    backup_manifest = manifest_path.read_bytes()
    dump_candles(series_path, _series(51), symbol=BTC, timeframe=TIMEFRAME)
    journal_dir = promote_journal_dir(tmp_path, BTC, TIMEFRAME)
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
            "planned_series_sha256": sha256_file(series_path),
            "planned_manifest_sha256": "ab" * 32,
        },
    )
    assert inspect_promote_journal(tmp_path, BTC, TIMEFRAME)["kind"] == "series_only"
    report = _scan(tmp_path, universe, now_ms=51 * TF)
    by_symbol = {item.symbol: item.sufficiency for item in report.assessments}
    assert by_symbol[BTC].eligible is False
    assert by_symbol[BTC].reason == "unsafe_promote_journal"
    assert by_symbol[ETH].eligible is True


def test_unknown_invalid_cache_prefix_is_reraised(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    universe = _symbols_universe(tmp_path, [BTC])
    _processed(tmp_path, BTC, _series(50))

    def boom(symbol: str, *, root: Path, now_ms: int):
        raise InvalidCacheError("unexpected-cache-shape: not a known prefix")

    monkeypatch.setattr(
        "tradingbot.data.ohlcv.universe_sufficiency.assess_processed_4h",
        boom,
    )
    with pytest.raises(InvalidCacheError, match="unexpected-cache-shape:"):
        _scan(tmp_path, universe, now_ms=50 * TF)


def test_unexpected_runtime_error_is_not_swallowed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    universe = _symbols_universe(tmp_path, [BTC])
    _processed(tmp_path, BTC, _series(50))

    def boom(symbol: str, *, root: Path, now_ms: int):
        raise RuntimeError("programming bug")

    monkeypatch.setattr(
        "tradingbot.data.ohlcv.universe_sufficiency.assess_processed_4h",
        boom,
    )
    with pytest.raises(RuntimeError, match="programming bug"):
        _scan(tmp_path, universe, now_ms=50 * TF)


def test_assessment_order_is_universe_order_not_filesystem(tmp_path: Path) -> None:
    universe = _symbols_universe(tmp_path, [ETH, BTC])
    _processed(tmp_path, BTC, _series(50))
    _processed(tmp_path, ETH, _series(49))
    report = _scan(tmp_path, universe, now_ms=50 * TF)
    assert [item.symbol for item in report.assessments] == [ETH, BTC]
    assert [item.symbol for item in report.rejected] == [ETH]
    assert [item.symbol for item in report.eligible] == [BTC]


def test_universe_and_processed_bytes_are_unchanged(tmp_path: Path) -> None:
    universe = _symbols_universe(tmp_path, [BTC, ETH])
    btc_path = _processed(tmp_path, BTC, _series(50))
    eth_path = _processed(tmp_path, ETH, _series(50))
    before = {
        universe: universe.read_bytes(),
        btc_path: btc_path.read_bytes(),
        eth_path: eth_path.read_bytes(),
    }
    _scan(tmp_path, universe, now_ms=50 * TF)
    for path, payload in before.items():
        assert path.read_bytes() == payload


def test_duplicate_universe_symbol_is_rejected_without_second_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    universe = _symbols_universe(tmp_path, [BTC, ETH, BTC])
    _processed(tmp_path, BTC, _series(50))
    _processed(tmp_path, ETH, _series(50))
    calls: list[str] = []

    def wrapped(symbol: str, *, root: Path, now_ms: int):
        calls.append(symbol)
        return assess_processed_4h(symbol, root=root, now_ms=now_ms)

    monkeypatch.setattr(
        "tradingbot.data.ohlcv.universe_sufficiency.assess_processed_4h",
        wrapped,
    )
    report = _scan(tmp_path, universe, now_ms=50 * TF)
    assert calls == [BTC, ETH]
    assert [item.symbol for item in report.assessments] == [BTC, ETH, BTC]
    assert report.assessments[2].sufficiency.reason == "duplicate_universe_symbol"
    assert report.assessments[2].sufficiency.eligible is False
    assert [item.symbol for item in report.eligible] == [BTC, ETH]


def test_missing_universe_file_stops_scan(tmp_path: Path) -> None:
    missing = tmp_path / "data" / "processed" / "universe.json"
    with pytest.raises(InvalidCacheError, match="missing cache"):
        _scan(tmp_path, missing, now_ms=50 * TF)


def test_universe_without_binance_symbol_values_stops_scan(tmp_path: Path) -> None:
    universe = _universe(tmp_path, [{"name": "Bitcoin"}])
    with pytest.raises(InvalidCacheError, match="universe has no binance_symbol values"):
        _scan(tmp_path, universe, now_ms=50 * TF)


def test_empty_binance_symbol_row_is_skipped_by_existing_reader(tmp_path: Path) -> None:
    universe = _universe(
        tmp_path,
        [{"binance_symbol": ""}, {"binance_symbol": BTC}],
    )
    _processed(tmp_path, BTC, _series(50))
    report = _scan(tmp_path, universe, now_ms=50 * TF)
    assert [item.symbol for item in report.assessments] == [BTC]
    assert report.eligible[0].sufficiency.eligible is True

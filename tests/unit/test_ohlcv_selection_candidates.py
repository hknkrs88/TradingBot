from pathlib import Path

import pytest

from tradingbot.data.io import atomic_write_json, sha256_file
from tradingbot.data.ohlcv.archive_promote import (
    JOURNAL_SCHEMA,
    inspect_promote_journal,
    promote_journal_dir,
)
from tradingbot.data.ohlcv.models import Candle
from tradingbot.data.ohlcv.selection_candidates import (
    SelectionUnsafeError,
    selection_candidates,
)
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


def _universe(root: Path, symbols: list[str]) -> Path:
    path = root / "data" / "processed" / "universe.json"
    atomic_write_json(path, {"universe": [{"binance_symbol": item} for item in symbols]})
    return path


def _processed(root: Path, symbol: str, candles: list[Candle]) -> Path:
    path = processed_path(root / "data" / "processed" / "ohlcv", symbol, TIMEFRAME)
    dump_candles(path, candles, symbol=symbol, timeframe=TIMEFRAME)
    return path


def _scan(root: Path, universe_path: Path, *, now_ms: int):
    return assess_universe_4h(universe_path=universe_path, root=root, now_ms=now_ms)


def test_two_eligible_symbols_keep_universe_order(tmp_path: Path) -> None:
    universe = _universe(tmp_path, [ETH, BTC])
    _processed(tmp_path, ETH, _series(50))
    _processed(tmp_path, BTC, _series(50))
    report = _scan(tmp_path, universe, now_ms=50 * TF)
    assert selection_candidates(report) == (ETH, BTC)


def test_journal_block_raises_selection_unsafe_error(tmp_path: Path) -> None:
    universe = _universe(tmp_path, [BTC, ETH])
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
    eligible_before = report.eligible
    with pytest.raises(SelectionUnsafeError, match="unsafe_promote_journal"):
        selection_candidates(report)
    assert report.eligible is eligible_before
    assert [item.symbol for item in report.eligible] == [ETH]


def test_missing_cache_and_too_few_closed_are_not_candidates(tmp_path: Path) -> None:
    universe = _universe(tmp_path, [BTC, ETH])
    _processed(tmp_path, ETH, _series(49))
    report = _scan(tmp_path, universe, now_ms=50 * TF)
    assert report.assessments[0].sufficiency.reason == "missing_processed_cache"
    assert report.assessments[1].sufficiency.reason == "too_few_closed_candles"
    assert selection_candidates(report) == ()


def test_gaps_and_not_caught_up_remain_candidates(tmp_path: Path) -> None:
    universe = _universe(tmp_path, [BTC, ETH])
    _processed(tmp_path, BTC, _series(49) + [_c(50)])
    _processed(tmp_path, ETH, _series(50))
    report = _scan(tmp_path, universe, now_ms=51 * TF)
    by_symbol = {item.symbol: item.sufficiency for item in report.assessments}
    assert by_symbol[BTC].has_gaps is True
    assert by_symbol[ETH].caught_up is False
    assert selection_candidates(report) == (BTC, ETH)


def test_empty_eligible_returns_empty_tuple(tmp_path: Path) -> None:
    universe = _universe(tmp_path, [BTC])
    _processed(tmp_path, BTC, _series(49))
    report = _scan(tmp_path, universe, now_ms=49 * TF)
    assert report.eligible == ()
    assert selection_candidates(report) == ()


def test_selection_candidates_does_not_mutate_report(tmp_path: Path) -> None:
    universe = _universe(tmp_path, [ETH, BTC])
    _processed(tmp_path, ETH, _series(50))
    _processed(tmp_path, BTC, _series(49))
    report = _scan(tmp_path, universe, now_ms=50 * TF)
    assessments = report.assessments
    eligible = report.eligible
    rejected = report.rejected
    assert selection_candidates(report) == (ETH,)
    assert report.assessments is assessments
    assert report.eligible is eligible
    assert report.rejected is rejected

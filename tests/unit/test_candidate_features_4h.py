from pathlib import Path

import pytest

from tradingbot.data.io import InvalidCacheError, atomic_write_json, sha256_file
from tradingbot.data.ohlcv.archive_promote import (
    JOURNAL_SCHEMA,
    inspect_promote_journal,
    promote_journal_dir,
)
from tradingbot.data.ohlcv.models import Candle
from tradingbot.data.ohlcv.store import dump_candles, processed_path
from tradingbot.data.ohlcv.selection_candidates import SelectionUnsafeError
from tradingbot.data.ohlcv.sufficiency import TIMEFRAME
from tradingbot.data.ohlcv.universe_sufficiency import assess_universe_4h
from tradingbot.selection.candidate_features import candidate_features_4h
from tradingbot.selection.rank_features import rank_features_4h
from tradingbot.strategy.models import CandleSeries

TF = 14_400_000
BTC = "BTC/USDT"
ETH = "ETH/USDT"


def _c(index: int, *, close: float = 1.0, volume: float = 1.0) -> Candle:
    return Candle(
        timestamp=index * TF,
        open=close,
        high=close,
        low=close,
        close=close,
        volume=volume,
    )


def _series(count: int, *, close_fn=None) -> list[Candle]:
    if close_fn is None:
        return [_c(index, close=50.0 + index) for index in range(count)]
    return [_c(index, close=close_fn(index)) for index in range(count)]


def _universe(root: Path, symbols: list[str]) -> Path:
    path = root / "data" / "processed" / "universe.json"
    atomic_write_json(path, {"universe": [{"binance_symbol": item} for item in symbols]})
    return path


def _dump(root: Path, symbol: str, candles: list[Candle], *, timeframe: str = TIMEFRAME) -> Path:
    path = processed_path(root / "data" / "processed" / "ohlcv", symbol, TIMEFRAME)
    dump_candles(path, candles, symbol=symbol, timeframe=timeframe)
    return path


def _scan(root: Path, universe: Path, *, now_ms: int):
    return assess_universe_4h(universe_path=universe, root=root, now_ms=now_ms)


def test_multiple_candidates_keep_order_and_hand_values(tmp_path: Path) -> None:
    universe = _universe(tmp_path, [ETH, BTC])
    eth = _series(50)
    btc = _series(50)
    _dump(tmp_path, ETH, eth)
    _dump(tmp_path, BTC, btc)
    report = _scan(tmp_path, universe, now_ms=50 * TF)
    result = candidate_features_4h(report, root=tmp_path)
    assert [item.symbol for item in result.features] == [ETH, BTC]
    assert result.rejected == ()
    expected = rank_features_4h(
        CandleSeries.from_closed(
            symbol=ETH, timeframe=TIMEFRAME, candles=tuple(eth), now_ms=50 * TF
        )
    )
    assert result.features[0].features == expected
    assert result.features[0].features.sma20 == 89.5
    assert result.features[0].features.sma50 == 74.5
    assert result.features[0].features.roc6_pct == (99.0 / 93.0 - 1) * 100


def test_open_candle_is_excluded_with_report_now_ms(tmp_path: Path) -> None:
    universe = _universe(tmp_path, [BTC])
    closed = _series(50)
    open_bar = _c(50, close=999.0)
    _dump(tmp_path, BTC, closed + [open_bar])
    now_ms = 50 * TF + 1
    report = _scan(tmp_path, universe, now_ms=now_ms)
    assert report.eligible[0].sufficiency.eligible is True
    result = candidate_features_4h(report, root=tmp_path)
    assert result.rejected == ()
    assert result.features[0].features.sma50 == 74.5
    assert result.features[0].features.roc6_pct == (99.0 / 93.0 - 1) * 100


def test_json_symbol_mismatch_rejects_only_that_candidate(tmp_path: Path) -> None:
    universe = _universe(tmp_path, [BTC, ETH])
    _dump(tmp_path, BTC, _series(50))
    _dump(tmp_path, ETH, _series(50))
    report = _scan(tmp_path, universe, now_ms=50 * TF)
    path = processed_path(tmp_path / "data" / "processed" / "ohlcv", ETH, TIMEFRAME)
    dump_candles(path, _series(50), symbol=BTC, timeframe=TIMEFRAME)
    result = candidate_features_4h(report, root=tmp_path)
    assert [item.symbol for item in result.features] == [BTC]
    assert [(item.symbol, item.reason) for item in result.rejected] == [
        (ETH, "symbol_mismatch")
    ]


def test_wrong_timeframe_rejects_only_that_candidate(tmp_path: Path) -> None:
    universe = _universe(tmp_path, [BTC, ETH])
    _dump(tmp_path, BTC, _series(50))
    _dump(tmp_path, ETH, _series(50))
    report = _scan(tmp_path, universe, now_ms=50 * TF)
    path = processed_path(tmp_path / "data" / "processed" / "ohlcv", ETH, TIMEFRAME)
    dump_candles(path, _series(50), symbol=ETH, timeframe="1h")
    result = candidate_features_4h(report, root=tmp_path)
    assert [item.symbol for item in result.features] == [BTC]
    assert result.rejected[0].symbol == ETH
    assert result.rejected[0].reason == "timeframe_mismatch"


def test_missing_cache_rejects_and_continues(tmp_path: Path) -> None:
    universe = _universe(tmp_path, [BTC, ETH])
    btc_path = _dump(tmp_path, BTC, _series(50))
    _dump(tmp_path, ETH, _series(50))
    report = _scan(tmp_path, universe, now_ms=50 * TF)
    btc_path.unlink()
    result = candidate_features_4h(report, root=tmp_path)
    assert [item.symbol for item in result.features] == [ETH]
    assert result.rejected[0].symbol == BTC
    assert result.rejected[0].reason == "missing_processed_cache"


def test_corrupt_json_rejects_and_continues(tmp_path: Path) -> None:
    universe = _universe(tmp_path, [BTC, ETH])
    path = _dump(tmp_path, BTC, _series(50))
    _dump(tmp_path, ETH, _series(50))
    report = _scan(tmp_path, universe, now_ms=50 * TF)
    path.write_text("{not-json", encoding="utf-8")
    result = candidate_features_4h(report, root=tmp_path)
    assert [item.symbol for item in result.features] == [ETH]
    assert result.rejected[0].reason == "corrupt_processed_cache"


def test_too_few_closed_after_reload_rejects(tmp_path: Path) -> None:
    universe = _universe(tmp_path, [BTC, ETH])
    _dump(tmp_path, BTC, _series(50))
    _dump(tmp_path, ETH, _series(50))
    report = _scan(tmp_path, universe, now_ms=50 * TF)
    _dump(tmp_path, ETH, _series(49))
    result = candidate_features_4h(report, root=tmp_path)
    assert [item.symbol for item in result.features] == [BTC]
    assert result.rejected[0].symbol == ETH
    assert result.rejected[0].reason == "too_few_closed_candles"


def test_gap_in_last_50_rejects_and_continues(tmp_path: Path) -> None:
    universe = _universe(tmp_path, [BTC, ETH])
    gapped = _series(49) + [_c(50, close=99.0)]
    _dump(tmp_path, BTC, gapped)
    _dump(tmp_path, ETH, _series(50))
    report = _scan(tmp_path, universe, now_ms=51 * TF)
    assert report.eligible[0].sufficiency.has_gaps is True
    result = candidate_features_4h(report, root=tmp_path)
    assert [item.symbol for item in result.features] == [ETH]
    assert result.rejected[0].symbol == BTC
    assert result.rejected[0].reason == "gap_in_last_50_4h"


def test_scan_time_journal_stops_entire_operation(tmp_path: Path) -> None:
    universe = _universe(tmp_path, [BTC, ETH])
    series_path = _dump(tmp_path, BTC, _series(50))
    _dump(tmp_path, ETH, _series(50))
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
    with pytest.raises(SelectionUnsafeError, match="unsafe_promote_journal"):
        candidate_features_4h(report, root=tmp_path)


def test_reload_journal_stops_entire_operation(tmp_path: Path) -> None:
    universe = _universe(tmp_path, [BTC, ETH])
    series_path = _dump(tmp_path, BTC, _series(50))
    _dump(tmp_path, ETH, _series(50))
    report = _scan(tmp_path, universe, now_ms=50 * TF)
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
    with pytest.raises(InvalidCacheError, match="unsafe_promote_journal:"):
        candidate_features_4h(report, root=tmp_path)


def test_files_and_report_are_unchanged(tmp_path: Path) -> None:
    universe = _universe(tmp_path, [BTC, ETH])
    btc_path = _dump(tmp_path, BTC, _series(50))
    eth_path = _dump(tmp_path, ETH, _series(49))
    report = _scan(tmp_path, universe, now_ms=50 * TF)
    before = {
        universe: universe.read_bytes(),
        btc_path: btc_path.read_bytes(),
        eth_path: eth_path.read_bytes(),
    }
    assessments = report.assessments
    eligible = report.eligible
    rejected = report.rejected
    result = candidate_features_4h(report, root=tmp_path)
    assert [item.symbol for item in result.features] == [BTC]
    assert report.assessments is assessments
    assert report.eligible is eligible
    assert report.rejected is rejected
    for path, payload in before.items():
        assert path.read_bytes() == payload


def test_rejection_order_follows_candidates(tmp_path: Path) -> None:
    universe = _universe(tmp_path, [ETH, BTC])
    _dump(tmp_path, ETH, _series(50))
    _dump(tmp_path, BTC, _series(50))
    report = _scan(tmp_path, universe, now_ms=50 * TF)
    eth_path = processed_path(tmp_path / "data" / "processed" / "ohlcv", ETH, TIMEFRAME)
    dump_candles(eth_path, _series(50), symbol=BTC, timeframe=TIMEFRAME)
    processed_path(tmp_path / "data" / "processed" / "ohlcv", BTC, TIMEFRAME).write_text(
        "{not-json", encoding="utf-8"
    )
    result = candidate_features_4h(report, root=tmp_path)
    assert result.features == ()
    assert [(item.symbol, item.reason) for item in result.rejected] == [
        (ETH, "symbol_mismatch"),
        (BTC, "corrupt_processed_cache"),
    ]


def test_unknown_invalid_cache_is_reraised(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    universe = _universe(tmp_path, [BTC])
    _dump(tmp_path, BTC, _series(50))
    report = _scan(tmp_path, universe, now_ms=50 * TF)

    def boom(root, symbol, timeframe):
        raise InvalidCacheError("unexpected-cache-shape: not a known prefix")

    monkeypatch.setattr(
        "tradingbot.selection.candidate_features.refuse_unsafe_promote_journal",
        boom,
    )
    with pytest.raises(InvalidCacheError, match="unexpected-cache-shape:"):
        candidate_features_4h(report, root=tmp_path)


def _rewrite_payload(root: Path, symbol: str, payload: dict) -> None:
    path = processed_path(root / "data" / "processed" / "ohlcv", symbol, TIMEFRAME)
    atomic_write_json(path, payload)


def _valid_candle_dicts(count: int = 50) -> list[dict]:
    return [item.to_dict() for item in _series(count)]


def test_missing_symbol_field_is_missing_source_identity(tmp_path: Path) -> None:
    universe = _universe(tmp_path, [BTC, ETH])
    _dump(tmp_path, BTC, _series(50))
    _dump(tmp_path, ETH, _series(50))
    report = _scan(tmp_path, universe, now_ms=50 * TF)
    _rewrite_payload(
        tmp_path,
        BTC,
        {"timeframe": TIMEFRAME, "candles": _valid_candle_dicts()},
    )
    result = candidate_features_4h(report, root=tmp_path)
    assert [item.symbol for item in result.features] == [ETH]
    assert [(item.symbol, item.reason) for item in result.rejected] == [
        (BTC, "missing_source_identity")
    ]


def test_empty_symbol_is_missing_source_identity(tmp_path: Path) -> None:
    universe = _universe(tmp_path, [BTC, ETH])
    _dump(tmp_path, BTC, _series(50))
    _dump(tmp_path, ETH, _series(50))
    report = _scan(tmp_path, universe, now_ms=50 * TF)
    _rewrite_payload(
        tmp_path,
        BTC,
        {"symbol": "", "timeframe": TIMEFRAME, "candles": _valid_candle_dicts()},
    )
    result = candidate_features_4h(report, root=tmp_path)
    assert [item.symbol for item in result.features] == [ETH]
    assert result.rejected[0].reason == "missing_source_identity"


def test_empty_timeframe_is_missing_source_identity(tmp_path: Path) -> None:
    universe = _universe(tmp_path, [BTC, ETH])
    _dump(tmp_path, BTC, _series(50))
    _dump(tmp_path, ETH, _series(50))
    report = _scan(tmp_path, universe, now_ms=50 * TF)
    _rewrite_payload(
        tmp_path,
        BTC,
        {"symbol": BTC, "timeframe": "", "candles": _valid_candle_dicts()},
    )
    result = candidate_features_4h(report, root=tmp_path)
    assert [item.symbol for item in result.features] == [ETH]
    assert result.rejected[0].reason == "missing_source_identity"


def test_none_timeframe_is_missing_source_identity(tmp_path: Path) -> None:
    universe = _universe(tmp_path, [BTC, ETH])
    _dump(tmp_path, BTC, _series(50))
    _dump(tmp_path, ETH, _series(50))
    report = _scan(tmp_path, universe, now_ms=50 * TF)
    _rewrite_payload(
        tmp_path,
        BTC,
        {"symbol": BTC, "timeframe": None, "candles": _valid_candle_dicts()},
    )
    result = candidate_features_4h(report, root=tmp_path)
    assert [item.symbol for item in result.features] == [ETH]
    assert result.rejected[0].reason == "missing_source_identity"


def test_non_string_symbol_is_missing_source_identity(tmp_path: Path) -> None:
    universe = _universe(tmp_path, [BTC, ETH])
    _dump(tmp_path, BTC, _series(50))
    _dump(tmp_path, ETH, _series(50))
    report = _scan(tmp_path, universe, now_ms=50 * TF)
    _rewrite_payload(
        tmp_path,
        BTC,
        {"symbol": 123, "timeframe": TIMEFRAME, "candles": _valid_candle_dicts()},
    )
    result = candidate_features_4h(report, root=tmp_path)
    assert [item.symbol for item in result.features] == [ETH]
    assert result.rejected[0].reason == "missing_source_identity"


def test_missing_candles_field_is_invalid_processed_payload(tmp_path: Path) -> None:
    universe = _universe(tmp_path, [BTC, ETH])
    _dump(tmp_path, BTC, _series(50))
    _dump(tmp_path, ETH, _series(50))
    report = _scan(tmp_path, universe, now_ms=50 * TF)
    _rewrite_payload(tmp_path, BTC, {"symbol": BTC, "timeframe": TIMEFRAME})
    result = candidate_features_4h(report, root=tmp_path)
    assert [item.symbol for item in result.features] == [ETH]
    assert result.rejected[0].reason == "invalid_processed_payload"


def test_candles_not_a_list_is_invalid_processed_payload(tmp_path: Path) -> None:
    universe = _universe(tmp_path, [BTC, ETH])
    _dump(tmp_path, BTC, _series(50))
    _dump(tmp_path, ETH, _series(50))
    report = _scan(tmp_path, universe, now_ms=50 * TF)
    _rewrite_payload(
        tmp_path,
        BTC,
        {"symbol": BTC, "timeframe": TIMEFRAME, "candles": {"not": "a list"}},
    )
    result = candidate_features_4h(report, root=tmp_path)
    assert [item.symbol for item in result.features] == [ETH]
    assert result.rejected[0].reason == "invalid_processed_payload"


def test_unaligned_timestamp_rejects_with_specific_reason(tmp_path: Path) -> None:
    universe = _universe(tmp_path, [BTC, ETH])
    _dump(tmp_path, BTC, _series(50))
    _dump(tmp_path, ETH, _series(50))
    report = _scan(tmp_path, universe, now_ms=50 * TF)
    candles = _series(50)
    candles[10] = Candle(
        timestamp=10 * TF + 60_000,
        open=1.0,
        high=1.0,
        low=1.0,
        close=1.0,
        volume=1.0,
    )
    _dump(tmp_path, BTC, candles)
    result = candidate_features_4h(report, root=tmp_path)
    assert [item.symbol for item in result.features] == [ETH]
    assert result.rejected[0].symbol == BTC
    assert result.rejected[0].reason == "unaligned_timestamp"


def test_negative_close_rejects_with_specific_reason(tmp_path: Path) -> None:
    universe = _universe(tmp_path, [BTC, ETH])
    _dump(tmp_path, BTC, _series(50))
    _dump(tmp_path, ETH, _series(50))
    report = _scan(tmp_path, universe, now_ms=50 * TF)
    candles = _series(50)
    candles[-1] = _c(49, close=-1.0)
    _dump(tmp_path, BTC, candles)
    result = candidate_features_4h(report, root=tmp_path)
    assert [item.symbol for item in result.features] == [ETH]
    assert result.rejected[0].reason == "non_positive_close"


def test_negative_volume_rejects_with_specific_reason(tmp_path: Path) -> None:
    universe = _universe(tmp_path, [BTC, ETH])
    _dump(tmp_path, BTC, _series(50))
    _dump(tmp_path, ETH, _series(50))
    report = _scan(tmp_path, universe, now_ms=50 * TF)
    candles = _series(50)
    candles[-1] = _c(49, close=99.0, volume=-0.1)
    _dump(tmp_path, BTC, candles)
    result = candidate_features_4h(report, root=tmp_path)
    assert [item.symbol for item in result.features] == [ETH]
    assert result.rejected[0].reason == "negative_volume"


def test_unexpected_runtime_error_is_not_recorded_as_rejection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    universe = _universe(tmp_path, [BTC, ETH])
    _dump(tmp_path, BTC, _series(50))
    _dump(tmp_path, ETH, _series(50))
    report = _scan(tmp_path, universe, now_ms=50 * TF)

    def boom(series: CandleSeries):
        raise RuntimeError("programming bug")

    monkeypatch.setattr(
        "tradingbot.selection.candidate_features.rank_features_4h",
        boom,
    )
    with pytest.raises(RuntimeError, match="programming bug"):
        candidate_features_4h(report, root=tmp_path)

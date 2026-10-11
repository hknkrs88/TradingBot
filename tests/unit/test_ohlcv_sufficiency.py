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
from tradingbot.data.ohlcv.sufficiency import (
    MIN_CLOSED_CANDLES,
    TIMEFRAME,
    assess_4h_sufficiency,
)

TF = 14_400_000
SYMBOL = "BTC/USDT"


def _c(index: int, **over: float) -> Candle:
    row = {"open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5, "volume": 10.0, **over}
    return Candle(timestamp=index * TF, **row)


def _series(count: int, *, start: int = 0) -> list[Candle]:
    return [_c(start + index) for index in range(count)]


def _assess(
    candles: list[Candle],
    *,
    now_ms: int,
    symbol: str = SYMBOL,
    source_symbol: str | None = SYMBOL,
    source_timeframe: str | None = TIMEFRAME,
):
    return assess_4h_sufficiency(
        symbol,
        candles,
        now_ms=now_ms,
        source_symbol=source_symbol,
        source_timeframe=source_timeframe,
    )


def test_49_closed_candles_are_rejected() -> None:
    candles = _series(49)
    now_ms = 49 * TF
    result = _assess(candles, now_ms=now_ms)
    assert result.eligible is False
    assert result.reason == "too_few_closed_candles"
    assert result.closed_count == 49


def test_exactly_50_closed_candles_are_eligible() -> None:
    candles = _series(50)
    now_ms = 50 * TF
    result = _assess(candles, now_ms=now_ms)
    assert result.eligible is True
    assert result.reason is None
    assert result.closed_count == MIN_CLOSED_CANDLES
    assert result.has_gaps is False
    assert result.identity_checked is True


def test_open_candle_does_not_count_toward_minimum() -> None:
    candles = _series(49) + [_c(49)]
    now_ms = 49 * TF + 1_000
    result = _assess(candles, now_ms=now_ms)
    assert result.closed_count == 49
    assert result.eligible is False
    assert result.reason == "too_few_closed_candles"


def test_future_candle_is_rejected() -> None:
    candles = _series(50)
    now_ms = 50 * TF
    candles.append(_c(60))
    result = _assess(candles, now_ms=now_ms)
    assert result.eligible is False
    assert result.reason == "future_timestamp"


def test_duplicate_timestamp_is_rejected() -> None:
    candles = _series(50)
    candles[1] = _c(0)
    result = _assess(candles, now_ms=50 * TF)
    assert result.eligible is False
    assert result.reason == "duplicate_timestamp"


def test_unaligned_timestamp_is_rejected() -> None:
    candles = _series(50)
    candles[3] = Candle(
        timestamp=3 * TF + 60_000,
        open=1.0,
        high=2.0,
        low=0.5,
        close=1.5,
        volume=10.0,
    )
    result = _assess(candles, now_ms=50 * TF)
    assert result.eligible is False
    assert result.reason == "unaligned_timestamp"


def test_symbol_mismatch_is_rejected() -> None:
    candles = _series(50)
    result = _assess(
        candles,
        now_ms=50 * TF,
        source_symbol="ETH/USDT",
    )
    assert result.eligible is False
    assert result.reason == "symbol_mismatch"


def test_gaps_are_reported_without_rejecting() -> None:
    candles = _series(49) + [_c(50)]
    now_ms = 51 * TF
    result = _assess(candles, now_ms=now_ms)
    assert result.closed_count == 50
    assert result.eligible is True
    assert result.has_gaps is True
    assert result.gap_timestamps == (49 * TF,)
    assert result.reason is None


def test_input_series_is_not_mutated() -> None:
    candles = _series(50)
    original = list(candles)
    _assess(candles, now_ms=50 * TF)
    assert candles == original
    assert [item.timestamp for item in candles] == [index * TF for index in range(50)]


def test_fixed_now_ms_is_deterministic() -> None:
    candles = _series(50)
    now_ms = 50 * TF
    first = _assess(candles, now_ms=now_ms)
    second = _assess(candles, now_ms=now_ms)
    assert first == second
    assert first.last_closed_timestamp == 49 * TF
    assert first.last_closed_age_ms == 0
    assert first.caught_up is True


def test_unsorted_timestamps_are_rejected() -> None:
    candles = _series(50)
    candles[0], candles[2] = candles[2], candles[0]
    result = _assess(candles, now_ms=50 * TF)
    assert result.eligible is False
    assert result.reason == "unsorted_timestamps"


def test_wrong_source_timeframe_is_rejected() -> None:
    candles = _series(50)
    result = _assess(candles, now_ms=50 * TF, source_timeframe="1h")
    assert result.eligible is False
    assert result.reason == "timeframe_mismatch"


def test_invalid_ohlc_is_rejected() -> None:
    candles = _series(50)
    candles[4] = _c(4, high=1.0, open=1.0, close=1.5, low=0.5)
    result = _assess(candles, now_ms=50 * TF)
    assert result.eligible is False
    assert result.reason == "invalid_ohlc"


def test_negative_volume_is_rejected() -> None:
    candles = _series(50)
    candles[4] = _c(4, volume=-1)
    result = _assess(candles, now_ms=50 * TF)
    assert result.eligible is False
    assert result.reason == "negative_volume"


def test_missing_source_identity_does_not_reject_by_itself() -> None:
    candles = _series(50)
    result = _assess(
        candles,
        now_ms=50 * TF,
        source_symbol=None,
        source_timeframe=None,
    )
    assert result.identity_checked is False
    assert result.eligible is True
    assert result.reason is None


def test_empty_series_is_rejected_safely() -> None:
    result = _assess([], now_ms=50 * TF)
    assert result.eligible is False
    assert result.reason == "too_few_closed_candles"
    assert result.closed_count == 0
    assert result.last_closed_timestamp is None
    assert result.last_closed_age_ms is None
    assert result.has_gaps is False


def _processed_4h(root: Path, candles: list[Candle], *, symbol: str = SYMBOL, timeframe: str = TIMEFRAME) -> Path:
    path = processed_path(root / "data" / "processed" / "ohlcv", symbol, timeframe)
    dump_candles(path, candles, symbol=symbol, timeframe=timeframe)
    return path


def test_processed_50_closed_with_identity_is_eligible(tmp_path: Path) -> None:
    candles = _series(50)
    _processed_4h(tmp_path, candles)
    result = assess_processed_4h(SYMBOL, root=tmp_path, now_ms=50 * TF)
    assert result.eligible is True
    assert result.reason is None
    assert result.identity_checked is True
    assert result.closed_count == MIN_CLOSED_CANDLES


def test_processed_49_closed_is_rejected(tmp_path: Path) -> None:
    _processed_4h(tmp_path, _series(49))
    result = assess_processed_4h(SYMBOL, root=tmp_path, now_ms=49 * TF)
    assert result.eligible is False
    assert result.reason == "too_few_closed_candles"


def test_processed_source_symbol_mismatch(tmp_path: Path) -> None:
    path = processed_path(tmp_path / "data" / "processed" / "ohlcv", SYMBOL, TIMEFRAME)
    dump_candles(path, _series(50), symbol="ETH/USDT", timeframe=TIMEFRAME)
    result = assess_processed_4h(SYMBOL, root=tmp_path, now_ms=50 * TF)
    assert result.eligible is False
    assert result.reason == "symbol_mismatch"


def test_processed_source_timeframe_1h_mismatch(tmp_path: Path) -> None:
    path = processed_path(tmp_path / "data" / "processed" / "ohlcv", SYMBOL, TIMEFRAME)
    dump_candles(path, _series(50), symbol=SYMBOL, timeframe="1h")
    result = assess_processed_4h(SYMBOL, root=tmp_path, now_ms=50 * TF)
    assert result.eligible is False
    assert result.reason == "timeframe_mismatch"


def test_processed_missing_identity_is_not_eligible(tmp_path: Path) -> None:
    path = processed_path(tmp_path / "data" / "processed" / "ohlcv", SYMBOL, TIMEFRAME)
    atomic_write_json(
        path,
        {"candles": [item.to_dict() for item in _series(50)]},
    )
    result = assess_processed_4h(SYMBOL, root=tmp_path, now_ms=50 * TF)
    assert result.eligible is False
    assert result.reason == "missing_source_identity"
    assert result.identity_checked is False


def _write_processed_payload(root: Path, payload: dict) -> None:
    path = processed_path(root / "data" / "processed" / "ohlcv", SYMBOL, TIMEFRAME)
    atomic_write_json(path, payload)


def test_processed_empty_source_symbol_is_missing_identity(tmp_path: Path) -> None:
    _write_processed_payload(
        tmp_path,
        {
            "symbol": "",
            "timeframe": TIMEFRAME,
            "candles": [item.to_dict() for item in _series(50)],
        },
    )
    result = assess_processed_4h(SYMBOL, root=tmp_path, now_ms=50 * TF)
    assert result.eligible is False
    assert result.reason == "missing_source_identity"
    assert result.identity_checked is False


def test_processed_whitespace_source_symbol_is_mismatch(tmp_path: Path) -> None:
    _write_processed_payload(
        tmp_path,
        {
            "symbol": " ",
            "timeframe": TIMEFRAME,
            "candles": [item.to_dict() for item in _series(50)],
        },
    )
    result = assess_processed_4h(SYMBOL, root=tmp_path, now_ms=50 * TF)
    assert result.eligible is False
    assert result.reason == "symbol_mismatch"
    assert result.identity_checked is True


def test_processed_non_string_source_symbol_is_missing_identity(tmp_path: Path) -> None:
    _write_processed_payload(
        tmp_path,
        {
            "symbol": 123,
            "timeframe": TIMEFRAME,
            "candles": [item.to_dict() for item in _series(50)],
        },
    )
    result = assess_processed_4h(SYMBOL, root=tmp_path, now_ms=50 * TF)
    assert result.eligible is False
    assert result.reason == "missing_source_identity"
    assert result.identity_checked is False


def test_processed_empty_source_timeframe_is_missing_identity(tmp_path: Path) -> None:
    _write_processed_payload(
        tmp_path,
        {
            "symbol": SYMBOL,
            "timeframe": "",
            "candles": [item.to_dict() for item in _series(50)],
        },
    )
    result = assess_processed_4h(SYMBOL, root=tmp_path, now_ms=50 * TF)
    assert result.eligible is False
    assert result.reason == "missing_source_identity"
    assert result.identity_checked is False


def test_processed_non_string_source_timeframe_is_missing_identity(tmp_path: Path) -> None:
    _write_processed_payload(
        tmp_path,
        {
            "symbol": SYMBOL,
            "timeframe": 123,
            "candles": [item.to_dict() for item in _series(50)],
        },
    )
    result = assess_processed_4h(SYMBOL, root=tmp_path, now_ms=50 * TF)
    assert result.eligible is False
    assert result.reason == "missing_source_identity"
    assert result.identity_checked is False


def test_processed_missing_candles_field_raises(tmp_path: Path) -> None:
    _write_processed_payload(tmp_path, {"symbol": SYMBOL, "timeframe": TIMEFRAME})
    with pytest.raises(InvalidCacheError, match="ohlcv payload missing candles list"):
        assess_processed_4h(SYMBOL, root=tmp_path, now_ms=50 * TF)


def test_processed_candles_not_a_list_raises(tmp_path: Path) -> None:
    _write_processed_payload(
        tmp_path,
        {"symbol": SYMBOL, "timeframe": TIMEFRAME, "candles": {"not": "a list"}},
    )
    with pytest.raises(InvalidCacheError, match="ohlcv payload missing candles list"):
        assess_processed_4h(SYMBOL, root=tmp_path, now_ms=50 * TF)


def test_processed_series_only_journal_raises(tmp_path: Path) -> None:
    series_path = _processed_4h(tmp_path, _series(50))
    manifest_path = tmp_path / "data" / "processed" / "ohlcv" / "manifest_4h.json"
    atomic_write_json(manifest_path, {"schema": "ohlcv_dataset_manifest/v1", "timeframe": "4h"})
    pre_series = sha256_file(series_path)
    pre_manifest = sha256_file(manifest_path)
    backup_series = series_path.read_bytes()
    backup_manifest = manifest_path.read_bytes()
    dump_candles(series_path, _series(51), symbol=SYMBOL, timeframe=TIMEFRAME)
    journal_dir = promote_journal_dir(tmp_path, SYMBOL, TIMEFRAME)
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
    assert inspect_promote_journal(tmp_path, SYMBOL, TIMEFRAME)["kind"] == "series_only"
    with pytest.raises(InvalidCacheError, match="unsafe_promote_journal:series_only:"):
        assess_processed_4h(SYMBOL, root=tmp_path, now_ms=51 * TF)


def test_processed_gaps_are_reported_without_rejecting(tmp_path: Path) -> None:
    candles = _series(49) + [_c(50)]
    _processed_4h(tmp_path, candles)
    result = assess_processed_4h(SYMBOL, root=tmp_path, now_ms=51 * TF)
    assert result.eligible is True
    assert result.has_gaps is True
    assert result.reason is None


def test_processed_json_bytes_are_unchanged(tmp_path: Path) -> None:
    path = _processed_4h(tmp_path, _series(50))
    before = path.read_bytes()
    assess_processed_4h(SYMBOL, root=tmp_path, now_ms=50 * TF)
    assert path.read_bytes() == before


def test_processed_caller_candle_list_is_not_mutated(tmp_path: Path) -> None:
    candles = _series(50)
    original = list(candles)
    _processed_4h(tmp_path, candles)
    assess_processed_4h(SYMBOL, root=tmp_path, now_ms=50 * TF)
    assert candles == original


def test_processed_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(InvalidCacheError, match="missing cache"):
        assess_processed_4h(SYMBOL, root=tmp_path, now_ms=50 * TF)


def test_processed_corrupt_json_raises(tmp_path: Path) -> None:
    path = processed_path(tmp_path / "data" / "processed" / "ohlcv", SYMBOL, TIMEFRAME)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not-json", encoding="utf-8")
    with pytest.raises(InvalidCacheError, match="corrupt cache"):
        assess_processed_4h(SYMBOL, root=tmp_path, now_ms=50 * TF)

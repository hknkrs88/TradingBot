from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest

from tradingbot.data.ohlcv.archive_overlap import (
    ArchiveOverlapError,
    analyze_archive_processed_files,
    analyze_series_overlap,
    write_overlap_report,
)
from tradingbot.data.ohlcv.store import dump_candles
from tradingbot.data.ohlcv.models import Candle

HOUR = 3_600_000


def _bar(ts: int, **fields: object) -> dict[str, object]:
    row: dict[str, object] = {
        "timestamp": ts,
        "open": Decimal("1.0"),
        "high": Decimal("1.0"),
        "low": Decimal("1.0"),
        "close": Decimal("1.0"),
        "volume": Decimal("1.0"),
    }
    row.update(fields)
    return row


def _payload(rows: list[dict[str, object]], *, symbol: str = "BTCUSDT", timeframe: str = "1h") -> dict:
    return {"symbol": symbol, "timeframe": timeframe, "candles": rows}


def _compare(archive_rows, processed_rows, **over):
    kwargs = {
        "symbol": "BTCUSDT",
        "timeframe": "1h",
        "archive_path": "archive.json",
        "processed_path_str": "processed.json",
        "mismatch_example_limit": 20,
    }
    kwargs.update(over)
    return analyze_series_overlap(
        _payload(archive_rows),
        _payload(processed_rows),
        **kwargs,
    )


def test_identical_series_match() -> None:
    rows = [_bar(0), _bar(HOUR)]
    report = _compare(rows, rows)
    assert report["status"] == "matched"
    assert report["counts"]["common_timestamps"] == 2
    assert report["counts"]["matched_ohlcv_bars"] == 2
    assert report["match_ratio"]["value"] == 1.0
    assert report["experimental"] is True
    assert report["schema"] == "binance_archive_overlap_report/v1"


def test_single_close_mismatch() -> None:
    archive = [_bar(0), _bar(HOUR, close=Decimal("1.5"))]
    processed = [_bar(0), _bar(HOUR)]
    report = _compare(archive, processed)
    assert report["status"] == "mismatched"
    assert report["counts"]["mismatched_ohlcv_bars"] == 1
    assert report["counts"]["field_mismatches"]["close"] == 1
    assert report["counts"]["field_mismatches"]["open"] == 0
    assert report["mismatch_examples"][0]["fields"]["close"]["archive"] == "1.5"


def test_independent_field_mismatches() -> None:
    archive = [
        _bar(0, open=Decimal("2")),
        _bar(HOUR, high=Decimal("3")),
        _bar(2 * HOUR, low=Decimal("0.1")),
        _bar(3 * HOUR, close=Decimal("9")),
        _bar(4 * HOUR, volume=Decimal("8")),
    ]
    processed = [_bar(i * HOUR) for i in range(5)]
    report = _compare(archive, processed)
    mismatches = report["counts"]["field_mismatches"]
    assert mismatches == {
        "open": 1,
        "high": 1,
        "low": 1,
        "close": 1,
        "volume": 1,
    }
    assert report["counts"]["mismatched_ohlcv_bars"] == 5


def test_archive_only_timestamp() -> None:
    report = _compare([_bar(0), _bar(HOUR)], [_bar(0)])
    assert report["counts"]["archive_only_timestamps"] == 1
    assert report["archive_only_sample"] == [HOUR]
    assert report["status"] == "matched"


def test_processed_only_timestamp() -> None:
    report = _compare([_bar(0)], [_bar(0), _bar(HOUR)])
    assert report["counts"]["processed_only_timestamps"] == 1
    assert report["processed_only_sample"] == [HOUR]


def test_partial_overlap() -> None:
    archive = [_bar(0), _bar(HOUR), _bar(2 * HOUR)]
    processed = [_bar(HOUR), _bar(2 * HOUR), _bar(3 * HOUR)]
    report = _compare(archive, processed)
    assert report["counts"]["common_timestamps"] == 2
    assert report["range"]["overlap_first"] == HOUR
    assert report["range"]["overlap_last"] == 2 * HOUR
    assert report["counts"]["archive_only_timestamps"] == 1
    assert report["counts"]["processed_only_timestamps"] == 1


def test_no_common_timestamps_null_ratio() -> None:
    report = _compare([_bar(0)], [_bar(HOUR)])
    assert report["counts"]["common_timestamps"] == 0
    assert report["match_ratio"]["value"] is None
    assert report["status"] == "matched"
    assert "no_common_timestamps; match_ratio is null" in report["warnings"]


def test_duplicate_timestamp_fail_closed() -> None:
    report = _compare([_bar(0), _bar(0, close=Decimal("2"))], [_bar(0)])
    assert report["status"] == "failed"
    assert report["duplicates"]["archive"] == 1
    assert report["mismatch_examples"] == []
    assert "duplicate timestamps" in report["warnings"][0]


def test_different_timeframes_rejected() -> None:
    with pytest.raises(ArchiveOverlapError, match="timeframe mismatch"):
        analyze_series_overlap(
            _payload([_bar(0)], timeframe="1h"),
            _payload([_bar(0)], timeframe="4h"),
            symbol="BTCUSDT",
            timeframe="1h",
            archive_path="a",
            processed_path_str="p",
        )


def test_unaligned_timestamp_rejected() -> None:
    with pytest.raises(ArchiveOverlapError, match="not aligned"):
        _compare([_bar(60_000)], [_bar(0)])


def test_deterministic_sort_and_content() -> None:
    archive = [_bar(HOUR), _bar(0)]
    processed = [_bar(0), _bar(HOUR)]
    first = _compare(archive, processed)
    second = _compare(list(reversed(archive)), processed)
    assert first["counts"] == second["counts"]
    assert first["range"] == second["range"]
    assert first["mismatch_examples"] == second["mismatch_examples"]
    assert first["status"] == second["status"]


def test_mismatch_example_limit() -> None:
    archive = [_bar(i * HOUR, close=Decimal("2")) for i in range(5)]
    processed = [_bar(i * HOUR) for i in range(5)]
    report = _compare(archive, processed, mismatch_example_limit=2)
    assert report["counts"]["mismatched_ohlcv_bars"] == 5
    assert len(report["mismatch_examples"]) == 2


def test_empty_series() -> None:
    report = _compare([], [])
    assert report["status"] == "matched"
    assert report["counts"]["archive_bars"] == 0
    assert report["counts"]["processed_bars"] == 0
    assert report["match_ratio"]["value"] is None


def test_decimal_not_binary_float() -> None:
    archive = [_bar(0, close=Decimal("0.1"))]
    processed = [_bar(0, close=Decimal("0.10"))]
    report = _compare(archive, processed)
    assert report["status"] == "matched"
    with pytest.raises(ArchiveOverlapError, match="binary float"):
        analyze_series_overlap(
            _payload([{"timestamp": 0, "open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0, "volume": 1.0}]),
            _payload([_bar(0)]),
            symbol="BTCUSDT",
            timeframe="1h",
            archive_path="a",
            processed_path_str="p",
        )


def test_report_write_failure_leaves_sources(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path
    processed_dir = root / "data" / "processed" / "ohlcv" / "BTCUSDT"
    processed_dir.mkdir(parents=True)
    archive_path = tmp_path / "experimental" / "candles.json"
    candles = [Candle(timestamp=0, open=1, high=1, low=1, close=1, volume=1)]
    dump_candles(processed_dir / "1h.json", candles, symbol="BTC/USDT", timeframe="1h")
    dump_candles(archive_path, candles, symbol="BTCUSDT", timeframe="1h")
    processed_before = (processed_dir / "1h.json").read_bytes()
    archive_before = archive_path.read_bytes()
    report_path = tmp_path / "experimental" / "overlap.json"

    def boom(path: Path, payload: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(
        "tradingbot.data.ohlcv.archive_overlap.atomic_write_json", boom
    )
    with pytest.raises(OSError, match="disk full"):
        analyze_archive_processed_files(
            root=root,
            symbol="BTC/USDT",
            timeframe="1h",
            archive_series_path=archive_path,
            report_path=report_path,
        )
    assert (processed_dir / "1h.json").read_bytes() == processed_before
    assert archive_path.read_bytes() == archive_before
    assert not report_path.exists()


def test_file_analysis_writes_experimental_report(tmp_path: Path) -> None:
    root = tmp_path
    processed_dir = root / "data" / "processed" / "ohlcv" / "BTCUSDT"
    processed_dir.mkdir(parents=True)
    archive_path = tmp_path / "exp" / "candles.json"
    candles = [Candle(timestamp=0, open=1, high=1, low=1, close=1, volume=1)]
    dump_candles(processed_dir / "1h.json", candles, symbol="BTC/USDT", timeframe="1h")
    dump_candles(archive_path, candles, symbol="BTCUSDT", timeframe="1h")
    report_path = tmp_path / "exp" / "overlap_report.json"
    report = analyze_archive_processed_files(
        root=root,
        symbol="BTCUSDT",
        timeframe="1h",
        archive_series_path=archive_path,
        report_path=report_path,
    )
    assert report["status"] == "matched"
    saved = json.loads(report_path.read_text(encoding="utf-8"))
    assert saved["schema"] == "binance_archive_overlap_report/v1"
    assert json.loads((processed_dir / "1h.json").read_text(encoding="utf-8"))["candles"][0]["close"] == 1


def test_refuses_report_under_processed(tmp_path: Path) -> None:
    with pytest.raises(ArchiveOverlapError, match="data/processed"):
        write_overlap_report({"schema": "x"}, tmp_path / "data" / "processed" / "ohlcv" / "r.json")

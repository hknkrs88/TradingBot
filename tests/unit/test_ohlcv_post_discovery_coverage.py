import importlib.util
import json
from datetime import datetime, timezone
from pathlib import Path

from tradingbot.data.io import atomic_write_json
from tradingbot.data.ohlcv.manifest import write_ohlcv_manifests
from tradingbot.data.ohlcv.models import Candle
from tradingbot.data.ohlcv.post_discovery_coverage import (
    assess_post_discovery_coverage,
    expected_slots,
    month_symbol_coverage,
)
from tradingbot.data.ohlcv.store import dump_candles

FOUR_H = 14_400_000
NOW = int(datetime(2027, 2, 2, tzinfo=timezone.utc).timestamp() * 1000)


def _c(ts: int) -> Candle:
    return Candle(timestamp=ts, open=1, high=2, low=0.5, close=1.5, volume=10)


def _config(tmp_path: Path) -> Path:
    path = tmp_path / "ohlcv.toml"
    path.write_text(
        "\n".join(
            [
                'provider = "binance_public"',
                'timeframes = ["4h"]',
                "candle_limit = 1000",
                'raw_dir = "data/raw/ohlcv"',
                'processed_dir = "data/processed/ohlcv"',
                "retry_max = 0",
                "backoff_seconds = 0",
                "drop_incomplete = true",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return path


def _universe(tmp_path: Path, symbols: list[str]) -> Path:
    path = tmp_path / "data" / "processed" / "universe.json"
    atomic_write_json(path, {"universe": [{"binance_symbol": item} for item in symbols]})
    return path


def _dump(tmp_path: Path, symbol: str, candles: list[Candle]) -> Path:
    path = tmp_path / "data" / "processed" / "ohlcv" / symbol.replace("/", "") / "4h.json"
    dump_candles(path, candles, symbol=symbol, timeframe="4h")
    return path


def _manifest(tmp_path: Path, universe: Path) -> None:
    write_ohlcv_manifests(
        universe_path=universe,
        processed_root=tmp_path / "data" / "processed" / "ohlcv",
        timeframes=("4h",),
        provider="binance_public",
        candle_limit=1000,
        drop_incomplete=True,
        now_ms=NOW,
        root=tmp_path,
    )


def _month_candles(year: int, month: int) -> list[Candle]:
    return [_c(ts) for ts in expected_slots(year, month)]


def _assess(tmp_path: Path, universe: Path) -> dict:
    return assess_post_discovery_coverage(
        root=tmp_path,
        universe_path=universe,
        processed_root=tmp_path / "data" / "processed" / "ohlcv",
        now_ms=NOW,
        drop_incomplete=True,
    )


def _script():
    path = Path(__file__).resolve().parents[2] / "scripts" / "report_4h_post_discovery_coverage.py"
    spec = importlib.util.spec_from_file_location("report_4h_post_discovery_coverage_cli", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _snapshot(root: Path) -> dict[str, bytes]:
    return {path.relative_to(root).as_posix(): path.read_bytes() for path in root.rglob("*") if path.is_file()}


def test_expected_month_slot_counts() -> None:
    assert len(expected_slots(2026, 11)) == 180
    assert len(expected_slots(2026, 12)) == 186
    assert len(expected_slots(2027, 1)) == 186


def test_complete_month_has_no_missing_slots() -> None:
    slots = expected_slots(2026, 11)
    coverage = month_symbol_coverage(slots, 2026, 11)
    assert coverage["found"] == 180
    assert coverage["first"] == slots[0]
    assert coverage["last"] == slots[-1]
    assert coverage["missing_count"] == 0
    assert coverage["missing_slots"] == []


def test_three_complete_months_are_ready(tmp_path: Path) -> None:
    candles = _month_candles(2026, 11) + _month_candles(2026, 12) + _month_candles(2027, 1)
    universe = _universe(tmp_path, ["BTC/USDT"])
    _dump(tmp_path, "BTC/USDT", candles)
    _manifest(tmp_path, universe)
    report = _assess(tmp_path, universe)
    assert report["status"] == "READY"
    assert report["errors"] == []
    assert [row["month"] for row in report["months"]] == ["2026-11", "2026-12", "2027-01"]
    assert [row["symbols_complete"] for row in report["months"]] == [1, 1, 1]
    assert report["months"][0]["total_candles"] == 180


def test_symbol_gap_does_not_hide_a_complete_month(tmp_path: Path) -> None:
    full = _month_candles(2026, 11) + _month_candles(2026, 12) + _month_candles(2027, 1)
    november = _month_candles(2026, 11)
    november.pop(4)
    partial = november + _month_candles(2026, 12) + _month_candles(2027, 1)
    universe = _universe(tmp_path, ["BTC/USDT", "ETH/USDT"])
    _dump(tmp_path, "BTC/USDT", full)
    _dump(tmp_path, "ETH/USDT", partial)
    _manifest(tmp_path, universe)
    report = _assess(tmp_path, universe)
    assert report["status"] == "READY"
    assert report["months"][0]["symbols_complete"] == 1
    assert report["months"][0]["symbols_with_data"] == 2
    assert report["months"][0]["symbols"][1]["missing_count"] == 1


def test_missing_slot_is_not_ready(tmp_path: Path) -> None:
    november = _month_candles(2026, 11)
    missing = november.pop(10).timestamp
    candles = november + _month_candles(2026, 12) + _month_candles(2027, 1)
    universe = _universe(tmp_path, ["BTC/USDT"])
    _dump(tmp_path, "BTC/USDT", candles)
    _manifest(tmp_path, universe)
    report = _assess(tmp_path, universe)
    row = report["months"][0]["symbols"][0]
    assert report["status"] == "NOT_READY"
    assert row["found"] == 179
    assert row["missing_count"] == 1
    assert row["missing_slots"] == [missing]
    assert report["months"][0]["symbols_with_data"] == 1
    assert report["months"][0]["symbols_complete"] == 0


def test_month_with_no_candles_is_zero(tmp_path: Path) -> None:
    universe = _universe(tmp_path, ["BTC/USDT"])
    _dump(tmp_path, "BTC/USDT", [_c(0), _c(FOUR_H)])
    _manifest(tmp_path, universe)
    report = _assess(tmp_path, universe)
    assert report["status"] == "NOT_READY"
    november = report["months"][0]
    assert november["total_candles"] == 0
    assert november["symbols_with_data"] == 0
    assert november["symbols"][0]["found"] == 0
    assert november["symbols"][0]["first"] is None
    assert november["symbols"][0]["last"] is None
    assert november["symbols"][0]["missing_count"] == 180


def test_unaligned_timestamp_is_invalid(tmp_path: Path) -> None:
    universe = _universe(tmp_path, ["BTC/USDT"])
    _dump(tmp_path, "BTC/USDT", [_c(1_800_000)])
    report = _assess(tmp_path, universe)
    assert report["status"] == "INVALID_DATA"
    assert report["months"] == []
    assert report["errors"] == [
        {"code": "unaligned_timestamp", "symbol": "BTC/USDT", "timestamp": 1_800_000}
    ]


def test_duplicate_timestamp_is_invalid(tmp_path: Path) -> None:
    universe = _universe(tmp_path, ["BTC/USDT"])
    _dump(
        tmp_path,
        "BTC/USDT",
        [
            _c(0),
            Candle(timestamp=0, open=9, high=9, low=9, close=9, volume=9),
        ],
    )
    report = _assess(tmp_path, universe)
    assert report["status"] == "INVALID_DATA"
    assert report["errors"] == [
        {"code": "duplicate_timestamp", "symbol": "BTC/USDT", "timestamps": [0]}
    ]


def test_manifest_hash_mismatch_is_invalid(tmp_path: Path) -> None:
    universe = _universe(tmp_path, ["BTC/USDT"])
    _dump(tmp_path, "BTC/USDT", [_c(0), _c(FOUR_H)])
    _manifest(tmp_path, universe)
    manifest_path = tmp_path / "data" / "processed" / "ohlcv" / "manifest_4h.json"
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    payload["series"][0]["sha256"] = "0" * 64
    atomic_write_json(manifest_path, payload)
    report = _assess(tmp_path, universe)
    assert report["status"] == "INVALID_DATA"
    assert report["errors"] == [{"code": "manifest_mismatch", "reason": "hash_mismatch:BTC/USDT"}]


def test_command_prints_report_without_writing(tmp_path: Path, capsys) -> None:
    universe = _universe(tmp_path, ["BTC/USDT"])
    _dump(tmp_path, "BTC/USDT", [_c(0), _c(FOUR_H)])
    _manifest(tmp_path, universe)
    config = _config(tmp_path)
    before = _snapshot(tmp_path)
    code = _script().main(
        [
            "--root",
            str(tmp_path),
            "--config",
            str(config),
            "--universe",
            "data/processed/universe.json",
            "--now-ms",
            str(NOW),
        ]
    )
    captured = capsys.readouterr()
    report = json.loads(captured.out)
    assert code == 0
    assert report["status"] == "NOT_READY"
    assert report["discovery_end"] == "2026-10-11T12:00:00Z"
    assert _snapshot(tmp_path) == before

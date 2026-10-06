import json
from pathlib import Path

import pytest

from tradingbot.data.io import atomic_write_json, sha256_file
from tradingbot.data.ohlcv.manifest import (
    MANIFEST_SCHEMA,
    build_ohlcv_manifest,
    write_ohlcv_manifest,
)
from tradingbot.data.ohlcv.models import Candle
from tradingbot.data.ohlcv.store import dump_candles
from tradingbot.data.ohlcv.sync import OHLCVConfig, sync_ohlcv

HOUR = 3_600_000
FOUR_H = 14_400_000
NOW = 4 * HOUR + 100


def _c(ts: int) -> Candle:
    return Candle(timestamp=ts, open=1, high=2, low=0.5, close=1.5, volume=10)


def _config(timeframes: tuple[str, ...] = ("1h",)) -> OHLCVConfig:
    return OHLCVConfig(
        provider="binance_public",
        timeframes=timeframes,
        candle_limit=100,
        raw_dir="raw",
        processed_dir="processed",
        retry_max=2,
        backoff_seconds=0.01,
        drop_incomplete=True,
    )


def _universe(tmp_path: Path, symbols: list[str]) -> Path:
    path = tmp_path / "universe.json"
    atomic_write_json(path, {"universe": [{"binance_symbol": item} for item in symbols]})
    return path


class FakeProvider:
    def __init__(self, responses: dict[tuple[str, str], list[Candle] | Exception]) -> None:
        self.responses = responses
        self.calls: list[dict] = []

    def fetch_ohlcv(self, symbol, timeframe, *, since=None, limit=None):
        self.calls.append(
            {"symbol": symbol, "timeframe": timeframe, "since": since, "limit": limit}
        )
        value = self.responses[(symbol, timeframe)]
        if isinstance(value, Exception):
            raise value
        return value


def _read_manifest(tmp_path: Path, timeframe: str) -> dict:
    path = tmp_path / "processed" / f"manifest_{timeframe}.json"
    return json.loads(path.read_text(encoding="utf-8"))


def test_single_timeframe_manifest_fields(tmp_path: Path) -> None:
    universe = _universe(tmp_path, ["BTC/USDT", "ETH/USDT"])
    provider = FakeProvider(
        {
            ("BTC/USDT", "1h"): [_c(0), _c(HOUR)],
            ("ETH/USDT", "1h"): [_c(0), _c(HOUR), _c(2 * HOUR)],
        }
    )
    sync_ohlcv(
        universe_path=universe,
        config=_config(),
        provider=provider,
        root=tmp_path,
        now_ms=4 * HOUR,
        sleep_fn=lambda _d: None,
    )
    manifest = _read_manifest(tmp_path, "1h")
    assert manifest["schema"] == MANIFEST_SCHEMA
    assert manifest["provider"] == "binance_public"
    assert manifest["timeframe"] == "1h"
    assert manifest["universe"]["path"] == "universe.json"
    assert manifest["universe"]["sha256"] == sha256_file(universe)
    assert manifest["universe"]["symbols"] == ["BTC/USDT", "ETH/USDT"]
    assert manifest["universe"]["symbol_count"] == 2
    assert manifest["universe"]["symbol_count"] == len(manifest["universe"]["symbols"])
    assert manifest["config"] == {
        "provider": "binance_public",
        "candle_limit": 100,
        "drop_incomplete": True,
    }
    by_symbol = {row["symbol"]: row for row in manifest["series"]}
    assert by_symbol["BTC/USDT"]["path"] == "BTCUSDT/1h.json"
    assert by_symbol["BTC/USDT"]["range_start"] == 0
    assert by_symbol["BTC/USDT"]["range_end"] == HOUR
    assert by_symbol["BTC/USDT"]["candle_count"] == 2
    assert by_symbol["ETH/USDT"]["candle_count"] == 3
    assert by_symbol["ETH/USDT"]["range_end"] == 2 * HOUR
    assert manifest["incomplete_symbols"] == []


def test_series_sha256_matches_processed_file(tmp_path: Path) -> None:
    universe = _universe(tmp_path, ["BTC/USDT"])
    provider = FakeProvider({("BTC/USDT", "1h"): [_c(0), _c(HOUR)]})
    sync_ohlcv(
        universe_path=universe,
        config=_config(),
        provider=provider,
        root=tmp_path,
        now_ms=4 * HOUR,
        sleep_fn=lambda _d: None,
    )
    processed = tmp_path / "processed" / "BTCUSDT" / "1h.json"
    manifest = _read_manifest(tmp_path, "1h")
    assert manifest["series"][0]["sha256"] == sha256_file(processed)


def test_universe_change_updates_hash_and_symbols(tmp_path: Path) -> None:
    universe = _universe(tmp_path, ["BTC/USDT"])
    dump_candles(
        tmp_path / "processed" / "BTCUSDT" / "1h.json",
        [_c(0), _c(HOUR)],
        symbol="BTC/USDT",
        timeframe="1h",
    )
    first = build_ohlcv_manifest(
        universe_path=universe,
        processed_root=tmp_path / "processed",
        timeframe="1h",
        provider="binance_public",
        candle_limit=100,
        drop_incomplete=True,
        now_ms=4 * HOUR,
        root=tmp_path,
    )
    _universe(tmp_path, ["BTC/USDT", "ETH/USDT"])
    second = build_ohlcv_manifest(
        universe_path=universe,
        processed_root=tmp_path / "processed",
        timeframe="1h",
        provider="binance_public",
        candle_limit=100,
        drop_incomplete=True,
        now_ms=4 * HOUR,
        root=tmp_path,
    )
    assert first["universe"]["sha256"] != second["universe"]["sha256"]
    assert second["universe"]["sha256"] == sha256_file(universe)
    assert second["universe"]["symbols"] == ["BTC/USDT", "ETH/USDT"]
    assert second["universe"]["symbol_count"] == 2
    assert second["incomplete_symbols"] == ["ETH/USDT"]


def test_stale_processed_symbol_not_in_manifest(tmp_path: Path) -> None:
    universe = _universe(tmp_path, ["BTC/USDT"])
    dump_candles(
        tmp_path / "processed" / "BTCUSDT" / "1h.json",
        [_c(0), _c(HOUR)],
        symbol="BTC/USDT",
        timeframe="1h",
    )
    dump_candles(
        tmp_path / "processed" / "SOLUSDT" / "1h.json",
        [_c(0), _c(HOUR)],
        symbol="SOL/USDT",
        timeframe="1h",
    )
    manifest = build_ohlcv_manifest(
        universe_path=universe,
        processed_root=tmp_path / "processed",
        timeframe="1h",
        provider="binance_public",
        candle_limit=100,
        drop_incomplete=True,
        now_ms=4 * HOUR,
        root=tmp_path,
    )
    assert [row["symbol"] for row in manifest["series"]] == ["BTC/USDT"]
    assert "SOL/USDT" not in manifest["universe"]["symbols"]
    assert "SOL/USDT" not in manifest["incomplete_symbols"]


def test_two_timeframes_write_isolated_manifests(tmp_path: Path) -> None:
    universe = _universe(tmp_path, ["BTC/USDT"])
    provider = FakeProvider(
        {
            ("BTC/USDT", "1h"): [_c(0), _c(HOUR), _c(2 * HOUR)],
            ("BTC/USDT", "4h"): [_c(0), _c(FOUR_H)],
        }
    )
    sync_ohlcv(
        universe_path=universe,
        config=_config(("1h", "4h")),
        provider=provider,
        root=tmp_path,
        now_ms=10 * HOUR,
        sleep_fn=lambda _d: None,
    )
    one_h = _read_manifest(tmp_path, "1h")
    four_h = _read_manifest(tmp_path, "4h")
    assert (tmp_path / "processed" / "manifest_1h.json").is_file()
    assert (tmp_path / "processed" / "manifest_4h.json").is_file()
    assert one_h["timeframe"] == "1h"
    assert four_h["timeframe"] == "4h"
    assert one_h["series"][0]["path"] == "BTCUSDT/1h.json"
    assert four_h["series"][0]["path"] == "BTCUSDT/4h.json"
    assert one_h["series"][0]["candle_count"] == 3
    assert four_h["series"][0]["candle_count"] == 2
    assert one_h["series"][0]["range_end"] == 2 * HOUR
    assert four_h["series"][0]["range_end"] == FOUR_H
    assert one_h["series"][0]["sha256"] != four_h["series"][0]["sha256"]


def test_missing_processed_is_incomplete_other_stays_in_series(tmp_path: Path) -> None:
    universe = _universe(tmp_path, ["ETH/USDT", "BTC/USDT", "XRP/USDT"])
    class PartialProvider:
        def __init__(self) -> None:
            self.calls: list[str] = []

        def fetch_ohlcv(self, symbol, timeframe, *, since=None, limit=None):
            self.calls.append(symbol)
            if symbol == "XRP/USDT":
                raise ConnectionError("down")
            return [_c(0), _c(HOUR)]

    sync_ohlcv(
        universe_path=universe,
        config=_config(),
        provider=PartialProvider(),
        root=tmp_path,
        now_ms=4 * HOUR,
        sleep_fn=lambda _d: None,
    )
    manifest = _read_manifest(tmp_path, "1h")
    assert [row["symbol"] for row in manifest["series"]] == ["ETH/USDT", "BTC/USDT"]
    assert manifest["incomplete_symbols"] == ["XRP/USDT"]


def test_invalid_processed_file_is_incomplete(tmp_path: Path) -> None:
    universe = _universe(tmp_path, ["BTC/USDT", "ETH/USDT"])
    dump_candles(
        tmp_path / "processed" / "BTCUSDT" / "1h.json",
        [_c(0), _c(HOUR)],
        symbol="BTC/USDT",
        timeframe="1h",
    )
    bad = tmp_path / "processed" / "ETHUSDT" / "1h.json"
    bad.parent.mkdir(parents=True, exist_ok=True)
    bad.write_text("{not-json", encoding="utf-8")
    manifest = build_ohlcv_manifest(
        universe_path=universe,
        processed_root=tmp_path / "processed",
        timeframe="1h",
        provider="binance_public",
        candle_limit=100,
        drop_incomplete=True,
        now_ms=4 * HOUR,
        root=tmp_path,
    )
    assert [row["symbol"] for row in manifest["series"]] == ["BTC/USDT"]
    assert manifest["incomplete_symbols"] == ["ETH/USDT"]


def test_cache_hit_rewrites_manifest_without_changing_file_hash(tmp_path: Path) -> None:
    universe = _universe(tmp_path, ["BTC/USDT"])
    dump_candles(
        tmp_path / "processed" / "BTCUSDT" / "1h.json",
        [_c(0), _c(HOUR), _c(2 * HOUR), _c(3 * HOUR)],
        symbol="BTC/USDT",
        timeframe="1h",
    )
    processed = tmp_path / "processed" / "BTCUSDT" / "1h.json"
    before = sha256_file(processed)
    provider = FakeProvider({})
    sync_ohlcv(
        universe_path=universe,
        config=_config(),
        provider=provider,
        root=tmp_path,
        now_ms=NOW,
        sleep_fn=lambda _d: None,
    )
    assert provider.calls == []
    assert sha256_file(processed) == before
    manifest = _read_manifest(tmp_path, "1h")
    assert manifest["series"][0]["sha256"] == before
    assert manifest["schema"] == MANIFEST_SCHEMA


def test_open_candle_excluded_from_range_and_count(tmp_path: Path) -> None:
    universe = _universe(tmp_path, ["BTC/USDT"])
    now = 5 * HOUR
    provider = FakeProvider(
        {("BTC/USDT", "1h"): [_c(0), _c(HOUR), _c(2 * HOUR), _c(3 * HOUR), _c(4 * HOUR), _c(5 * HOUR)]}
    )
    sync_ohlcv(
        universe_path=universe,
        config=_config(),
        provider=provider,
        root=tmp_path,
        now_ms=now,
        sleep_fn=lambda _d: None,
    )
    processed = json.loads(
        (tmp_path / "processed" / "BTCUSDT" / "1h.json").read_text(encoding="utf-8")
    )
    stamps = [row["timestamp"] for row in processed["candles"]]
    assert 5 * HOUR not in stamps
    manifest = _read_manifest(tmp_path, "1h")
    assert manifest["series"][0]["range_end"] == 4 * HOUR
    assert manifest["series"][0]["candle_count"] == len(stamps)
    assert manifest["series"][0]["candle_count"] == 5


def test_ordering_follows_universe(tmp_path: Path) -> None:
    universe = _universe(tmp_path, ["ETH/USDT", "BTC/USDT", "XRP/USDT"])
    dump_candles(
        tmp_path / "processed" / "XRPUSDT" / "1h.json",
        [_c(0), _c(HOUR)],
        symbol="XRP/USDT",
        timeframe="1h",
    )
    dump_candles(
        tmp_path / "processed" / "ETHUSDT" / "1h.json",
        [_c(0), _c(HOUR)],
        symbol="ETH/USDT",
        timeframe="1h",
    )
    manifest = build_ohlcv_manifest(
        universe_path=universe,
        processed_root=tmp_path / "processed",
        timeframe="1h",
        provider="binance_public",
        candle_limit=100,
        drop_incomplete=True,
        now_ms=4 * HOUR,
        root=tmp_path,
    )
    assert manifest["universe"]["symbols"] == ["ETH/USDT", "BTC/USDT", "XRP/USDT"]
    assert [row["symbol"] for row in manifest["series"]] == ["ETH/USDT", "XRP/USDT"]
    assert manifest["incomplete_symbols"] == ["BTC/USDT"]


def test_write_ohlcv_manifest_uses_atomic_write_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[Path] = []
    real = atomic_write_json

    def wrapped(path: Path, payload: object) -> None:
        calls.append(path)
        real(path, payload)

    monkeypatch.setattr("tradingbot.data.ohlcv.manifest.atomic_write_json", wrapped)
    payload = build_ohlcv_manifest(
        universe_path=_universe(tmp_path, ["BTC/USDT"]),
        processed_root=tmp_path / "processed",
        timeframe="1h",
        provider="binance_public",
        candle_limit=100,
        drop_incomplete=True,
        now_ms=4 * HOUR,
        root=tmp_path,
        generated_at="2026-10-06T17:00:00+00:00",
    )
    out = write_ohlcv_manifest(tmp_path / "processed", payload)
    assert calls == [out]
    assert out == tmp_path / "processed" / "manifest_1h.json"
    assert not (tmp_path / "processed" / "manifest_1h.json.tmp").exists()


def test_manifest_write_failure_is_not_silent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    universe = _universe(tmp_path, ["BTC/USDT"])
    provider = FakeProvider({("BTC/USDT", "1h"): [_c(0), _c(HOUR)]})

    def boom(path: Path, payload: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr("tradingbot.data.ohlcv.manifest.atomic_write_json", boom)
    with pytest.raises(OSError, match="disk full"):
        sync_ohlcv(
            universe_path=universe,
            config=_config(),
            provider=provider,
            root=tmp_path,
            now_ms=4 * HOUR,
            sleep_fn=lambda _d: None,
        )
    assert (tmp_path / "processed" / "sync_report.json").is_file()

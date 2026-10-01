import json
from pathlib import Path

from tradingbot.data.ohlcv.models import Candle
from tradingbot.data.ohlcv.store import dump_candles, load_candles, merge_candles, next_since

HOUR = 3_600_000
NOW = 10 * HOUR
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "ohlcv"


def _c(ts: int) -> Candle:
    return Candle(timestamp=ts, open=1, high=2, low=0.5, close=1.5, volume=10)


def test_fixture_candles_roundtrip(tmp_path: Path) -> None:
    rows = json.loads((FIXTURES / "candles_valid.json").read_text(encoding="utf-8"))
    candles = [Candle.from_dict(row) for row in rows]
    path = tmp_path / "BTCUSDT" / "1h.json"
    dump_candles(path, candles, symbol="BTC/USDT", timeframe="1h")
    loaded = load_candles(path)
    assert [c.timestamp for c in loaded] == [0, HOUR]


def test_merge_keeps_existing_on_overlap() -> None:
    existing = [_c(0), _c(HOUR)]
    incoming = [_c(HOUR), _c(2 * HOUR)]
    merged, issues = merge_candles(
        existing, incoming, "1h", now_ms=NOW, drop_incomplete=True
    )
    assert [c.timestamp for c in merged] == [0, HOUR, 2 * HOUR]
    assert any(item["reason"] == "duplicate_timestamp" for item in issues)


def test_next_since_is_last_closed_plus_tf() -> None:
    assert next_since(3 * HOUR, "1h") == 4 * HOUR


def test_merge_strips_invalid_and_incomplete_from_existing() -> None:
    now = 4 * HOUR
    existing = [
        _c(0),
        Candle(timestamp=HOUR, open=1.0, high=1.0, low=0.5, close=1.5, volume=10.0),
        Candle(timestamp=2 * HOUR, open=1.0, high=2.0, low=0.5, close=1.5, volume=-1.0),
        _c(4 * HOUR),
    ]
    incoming = [_c(3 * HOUR)]
    merged, issues = merge_candles(
        existing, incoming, "1h", now_ms=now, drop_incomplete=True
    )
    assert [c.timestamp for c in merged] == [0, 3 * HOUR]
    reasons = {item["reason"] for item in issues}
    assert "invalid_ohlc" in reasons
    assert "negative_volume" in reasons
    assert "incomplete_candle" in reasons
    assert "missing_candles" in reasons


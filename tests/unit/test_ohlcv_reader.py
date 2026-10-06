import json
from pathlib import Path

import pytest

from tradingbot.data.io import InvalidCacheError, atomic_write_json
from tradingbot.data.ohlcv.models import Candle
from tradingbot.data.ohlcv.reader import load_processed
from tradingbot.data.ohlcv.store import dump_candles
from tradingbot.data.ohlcv.timeframes import UnsupportedTimeframeError

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "ohlcv"
HOUR = 3_600_000


def _processed_root(tmp_path: Path) -> Path:
    return tmp_path / "data" / "processed" / "ohlcv"


def test_load_processed_returns_candles_from_store_json(tmp_path: Path) -> None:
    rows = json.loads((FIXTURES / "candles_valid.json").read_text(encoding="utf-8"))
    candles = [Candle.from_dict(row) for row in rows]
    dump_candles(
        _processed_root(tmp_path) / "BTCUSDT" / "1h.json",
        candles,
        symbol="BTC/USDT",
        timeframe="1h",
    )
    loaded = load_processed("BTC/USDT", "1h", root=tmp_path)
    assert isinstance(loaded, list)
    assert all(isinstance(item, Candle) for item in loaded)
    assert len(loaded) == 2
    assert loaded[0] == Candle(
        timestamp=0, open=1.0, high=2.0, low=0.5, close=1.5, volume=10.0
    )
    assert loaded[1].timestamp == HOUR
    assert loaded[1].open == 1.5
    assert loaded[1].high == 2.5
    assert loaded[1].low == 1.0
    assert loaded[1].close == 2.0
    assert loaded[1].volume == 11.0


def test_load_processed_preserves_timestamp_order(tmp_path: Path) -> None:
    dump_candles(
        _processed_root(tmp_path) / "ETHUSDT" / "1h.json",
        [
            Candle(timestamp=HOUR, open=1, high=2, low=0.5, close=1.5, volume=1),
            Candle(timestamp=0, open=1, high=2, low=0.5, close=1.5, volume=1),
        ],
        symbol="ETH/USDT",
        timeframe="1h",
    )
    loaded = load_processed("ETH/USDT", "1h", root=tmp_path)
    assert [c.timestamp for c in loaded] == [HOUR, 0]


def test_load_processed_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(InvalidCacheError, match="missing cache"):
        load_processed("BTC/USDT", "1h", root=tmp_path)


def test_load_processed_corrupt_json_raises(tmp_path: Path) -> None:
    path = _processed_root(tmp_path) / "BTCUSDT" / "1h.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not-json", encoding="utf-8")
    with pytest.raises(InvalidCacheError, match="corrupt cache"):
        load_processed("BTC/USDT", "1h", root=tmp_path)


def test_load_processed_invalid_candle_raises(tmp_path: Path) -> None:
    path = _processed_root(tmp_path) / "BTCUSDT" / "1h.json"
    atomic_write_json(
        path,
        {
            "symbol": "BTC/USDT",
            "timeframe": "1h",
            "candles": [{"timestamp": 0, "open": 1.0}],
        },
    )
    with pytest.raises(InvalidCacheError, match="invalid processed ohlcv"):
        load_processed("BTC/USDT", "1h", root=tmp_path)


def test_reader_module_has_no_provider_or_network_imports() -> None:
    import tradingbot.data.ohlcv.reader as reader_mod

    assert "ccxt" not in reader_mod.__dict__
    assert "create_public_binance" not in reader_mod.__dict__
    assert "BinancePublicOHLCVProvider" not in reader_mod.__dict__
    source = Path(reader_mod.__file__).read_text(encoding="utf-8")
    assert "ccxt" not in source
    assert "binance_ohlcv" not in source
    assert "httpx" not in source


def test_reader_rejects_unsupported_timeframe(tmp_path: Path) -> None:
    with pytest.raises(UnsupportedTimeframeError):
        load_processed("BTC/USDT", "15m", root=tmp_path)

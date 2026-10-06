"""Read processed OHLCV from disk. No network, no provider, no orders."""

from __future__ import annotations

from pathlib import Path

from tradingbot.data.io import InvalidCacheError
from tradingbot.data.ohlcv.models import Candle
from tradingbot.data.ohlcv.store import load_candles, processed_path
from tradingbot.data.ohlcv.timeframes import timeframe_ms


def load_processed(symbol: str, timeframe: str, *, root: Path) -> list[Candle]:
    timeframe_ms(timeframe)
    path = processed_path(Path(root) / "data" / "processed" / "ohlcv", symbol, timeframe)
    try:
        return load_candles(path)
    except InvalidCacheError:
        raise
    except (KeyError, TypeError, ValueError) as exc:
        raise InvalidCacheError(f"invalid processed ohlcv: {path}") from exc

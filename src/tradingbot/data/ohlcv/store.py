from __future__ import annotations

from pathlib import Path
from typing import Any

from tradingbot.data.io import InvalidCacheError, atomic_write_json, load_json
from tradingbot.data.ohlcv.models import Candle
from tradingbot.data.ohlcv.quality import attach_missing_issues, is_incomplete, sanitize_candles
from tradingbot.data.ohlcv.timeframes import timeframe_ms


def safe_symbol(symbol: str) -> str:
    return symbol.replace("/", "")


def raw_path(raw_dir: Path, provider: str, symbol: str, timeframe: str) -> Path:
    return raw_dir / provider / safe_symbol(symbol) / f"{timeframe}.json"


def processed_path(processed_dir: Path, symbol: str, timeframe: str) -> Path:
    return processed_dir / safe_symbol(symbol) / f"{timeframe}.json"


def candles_from_payload(payload: Any) -> list[Candle]:
    rows = payload.get("candles") if isinstance(payload, dict) else payload
    if not isinstance(rows, list):
        raise InvalidCacheError("ohlcv payload missing candles list")
    return [Candle.from_dict(row) for row in rows]


def load_candles(path: Path) -> list[Candle]:
    return candles_from_payload(load_json(path))


def dump_candles(
    path: Path,
    candles: list[Candle],
    *,
    symbol: str,
    timeframe: str,
    extra: dict[str, Any] | None = None,
) -> None:
    payload: dict[str, Any] = {
        "symbol": symbol,
        "timeframe": timeframe,
        "candles": [item.to_dict() for item in candles],
    }
    if extra:
        payload.update(extra)
    atomic_write_json(path, payload)


def last_closed_timestamp(candles: list[Candle], timeframe: str, now_ms: int) -> int | None:
    tf_ms = timeframe_ms(timeframe)
    closed = [item.timestamp for item in candles if not is_incomplete(item, tf_ms, now_ms)]
    if not closed:
        return None
    return max(closed)


def is_caught_up(last_closed: int | None, timeframe: str, now_ms: int) -> bool:
    if last_closed is None:
        return False
    tf_ms = timeframe_ms(timeframe)
    return last_closed + (2 * tf_ms) > now_ms


def next_since(last_closed: int, timeframe: str) -> int:
    return last_closed + timeframe_ms(timeframe)


def merge_candles(
    existing: list[Candle],
    incoming: list[Candle],
    timeframe: str,
    *,
    now_ms: int,
    drop_incomplete: bool,
) -> tuple[list[Candle], list[dict[str, Any]]]:
    existing_ok, issues = sanitize_candles(
        existing, timeframe, now_ms=now_ms, drop_incomplete=drop_incomplete
    )
    incoming_ok, incoming_issues = sanitize_candles(
        incoming, timeframe, now_ms=now_ms, drop_incomplete=drop_incomplete
    )
    issues.extend(incoming_issues)
    by_ts = {item.timestamp: item for item in existing_ok}
    for candle in incoming_ok:
        if candle.timestamp in by_ts:
            issues.append({"reason": "duplicate_timestamp", "timestamp": candle.timestamp})
            continue
        by_ts[candle.timestamp] = candle
    merged = sorted(by_ts.values(), key=lambda item: item.timestamp)
    attach_missing_issues(merged, timeframe, issues)
    return merged, issues

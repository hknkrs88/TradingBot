from __future__ import annotations

from collections import Counter
from typing import Any

from tradingbot.data.ohlcv.models import Candle
from tradingbot.data.ohlcv.timeframes import timeframe_ms


def is_incomplete(candle: Candle, tf_ms: int, now_ms: int) -> bool:
    return candle.timestamp + tf_ms > now_ms


class UnalignedTimestampError(Exception):
    def __init__(self, *, symbol: str, timeframe: str, timestamp: int) -> None:
        self.symbol = symbol
        self.timeframe = timeframe
        self.timestamp = timestamp
        super().__init__(
            f"unaligned_timestamp symbol={symbol} timeframe={timeframe} timestamp={timestamp}"
        )


def require_aligned_4h(symbol: str, candles: list[Candle]) -> None:
    """Reject 4h timestamps that are not on the UTC 4h grid. Does not rewrite data."""
    tf_ms = timeframe_ms("4h")
    for candle in candles:
        if candle.timestamp % tf_ms != 0:
            raise UnalignedTimestampError(
                symbol=symbol,
                timeframe="4h",
                timestamp=candle.timestamp,
            )


def candle_issue(candle: Candle, *, now_ms: int) -> str | None:
    if candle.timestamp > now_ms:
        return "future_timestamp"
    if candle.volume < 0:
        return "negative_volume"
    if candle.high < candle.low:
        return "invalid_ohlc"
    if candle.high < max(candle.open, candle.close):
        return "invalid_ohlc"
    if candle.low > min(candle.open, candle.close):
        return "invalid_ohlc"
    return None


def find_missing_timestamps(timestamps: list[int], tf_ms: int) -> list[int]:
    missing: list[int] = []
    for left, right in zip(timestamps, timestamps[1:]):
        expected = left + tf_ms
        while expected < right:
            missing.append(expected)
            expected += tf_ms
    return missing


def sanitize_candles(
    candles: list[Candle],
    timeframe: str,
    *,
    now_ms: int,
    drop_incomplete: bool = True,
) -> tuple[list[Candle], list[dict[str, Any]]]:
    tf_ms = timeframe_ms(timeframe)
    issues: list[dict[str, Any]] = []
    kept: list[Candle] = []

    for candle in candles:
        reason = candle_issue(candle, now_ms=now_ms)
        if reason:
            issues.append({"reason": reason, "timestamp": candle.timestamp})
            continue
        if drop_incomplete and is_incomplete(candle, tf_ms, now_ms):
            issues.append({"reason": "incomplete_candle", "timestamp": candle.timestamp})
            continue
        kept.append(candle)

    counts = Counter(item.timestamp for item in kept)
    duplicates = {ts for ts, n in counts.items() if n > 1}
    for ts in sorted(duplicates):
        issues.append({"reason": "duplicate_timestamp", "timestamp": ts})
    unique = [item for item in kept if item.timestamp not in duplicates]
    unique.sort(key=lambda item: item.timestamp)
    return unique, issues


def attach_missing_issues(
    candles: list[Candle],
    timeframe: str,
    issues: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    tf_ms = timeframe_ms(timeframe)
    for ts in find_missing_timestamps([item.timestamp for item in candles], tf_ms):
        issues.append({"reason": "missing_candles", "timestamp": ts})
    return issues


def filter_candles(
    candles: list[Candle],
    timeframe: str,
    *,
    now_ms: int,
    drop_incomplete: bool = True,
) -> tuple[list[Candle], list[dict[str, Any]]]:
    unique, issues = sanitize_candles(
        candles, timeframe, now_ms=now_ms, drop_incomplete=drop_incomplete
    )
    attach_missing_issues(unique, timeframe, issues)
    return unique, issues

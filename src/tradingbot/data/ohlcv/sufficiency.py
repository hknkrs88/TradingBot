"""Read-only 4h series sufficiency. No I/O, network, sync, or orders.

Candle rows do not carry symbol or timeframe. Identity is checked only when
source_symbol / source_timeframe are supplied (for example from dump_candles
JSON). Omitting them is reported, not guessed.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from tradingbot.data.ohlcv.models import Candle
from tradingbot.data.ohlcv.quality import candle_issue, find_missing_timestamps, is_incomplete
from tradingbot.data.ohlcv.store import is_caught_up
from tradingbot.data.ohlcv.timeframes import timeframe_ms

TIMEFRAME = "4h"
MIN_CLOSED_CANDLES = 50


@dataclass(frozen=True)
class SufficiencyResult:
    eligible: bool
    reason: str | None
    closed_count: int
    has_gaps: bool
    gap_timestamps: tuple[int, ...]
    last_closed_timestamp: int | None
    last_closed_age_ms: int | None
    caught_up: bool
    identity_checked: bool


def assess_4h_sufficiency(
    symbol: str,
    candles: Sequence[Candle],
    *,
    now_ms: int,
    source_symbol: str | None = None,
    source_timeframe: str | None = None,
) -> SufficiencyResult:
    """Classify a 4h series as eligible for later selection. Does not mutate candles."""
    tf_ms = timeframe_ms(TIMEFRAME)
    snapshot = tuple(candles)
    identity_checked = source_symbol is not None and source_timeframe is not None

    def finish(
        *,
        eligible: bool,
        reason: str | None,
        closed: Sequence[Candle],
    ) -> SufficiencyResult:
        stamps = [item.timestamp for item in closed]
        gaps = tuple(find_missing_timestamps(list(stamps), tf_ms)) if stamps else ()
        last_closed = stamps[-1] if stamps else None
        age = None if last_closed is None else now_ms - (last_closed + tf_ms)
        return SufficiencyResult(
            eligible=eligible,
            reason=reason,
            closed_count=len(closed),
            has_gaps=bool(gaps),
            gap_timestamps=gaps,
            last_closed_timestamp=last_closed,
            last_closed_age_ms=age,
            caught_up=is_caught_up(last_closed, TIMEFRAME, now_ms),
            identity_checked=identity_checked,
        )

    if source_timeframe is not None and source_timeframe != TIMEFRAME:
        return finish(eligible=False, reason="timeframe_mismatch", closed=())
    if source_symbol is not None and source_symbol != symbol:
        return finish(eligible=False, reason="symbol_mismatch", closed=())

    for candle in snapshot:
        issue = candle_issue(candle, now_ms=now_ms)
        if issue is not None:
            closed = _closed_candles(snapshot, tf_ms, now_ms)
            return finish(eligible=False, reason=issue, closed=closed)

    stamps = [item.timestamp for item in snapshot]
    if len(stamps) != len(set(stamps)):
        closed = _closed_candles(snapshot, tf_ms, now_ms)
        return finish(eligible=False, reason="duplicate_timestamp", closed=closed)
    if any(ts % tf_ms != 0 for ts in stamps):
        closed = _closed_candles(snapshot, tf_ms, now_ms)
        return finish(eligible=False, reason="unaligned_timestamp", closed=closed)
    if stamps != sorted(stamps):
        closed = _closed_candles(snapshot, tf_ms, now_ms)
        return finish(eligible=False, reason="unsorted_timestamps", closed=closed)

    closed = _closed_candles(snapshot, tf_ms, now_ms)
    if len(closed) < MIN_CLOSED_CANDLES:
        return finish(eligible=False, reason="too_few_closed_candles", closed=closed)
    return finish(eligible=True, reason=None, closed=closed)


def _closed_candles(
    candles: Sequence[Candle], tf_ms: int, now_ms: int
) -> tuple[Candle, ...]:
    return tuple(
        item
        for item in candles
        if candle_issue(item, now_ms=now_ms) is None
        and not is_incomplete(item, tf_ms, now_ms)
    )

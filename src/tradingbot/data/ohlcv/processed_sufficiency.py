"""Load processed 4h JSON and assess sufficiency. Read-only; no sync or orders.

Keeps dump_candles identity fields. Does not change load_processed.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from tradingbot.data.io import InvalidCacheError, load_json
from tradingbot.data.ohlcv.archive_promote import refuse_unsafe_promote_journal
from tradingbot.data.ohlcv.store import candles_from_payload, is_caught_up, processed_path
from tradingbot.data.ohlcv.sufficiency import (
    TIMEFRAME,
    SufficiencyResult,
    assess_4h_sufficiency,
)


def assess_processed_4h(symbol: str, *, root: Path, now_ms: int) -> SufficiencyResult:
    """Assess on-disk 4h processed JSON for later selection. Does not write."""
    refuse_unsafe_promote_journal(root, symbol, TIMEFRAME)
    path = processed_path(Path(root) / "data" / "processed" / "ohlcv", symbol, TIMEFRAME)
    payload = load_json(path)
    if not isinstance(payload, dict):
        raise InvalidCacheError(f"invalid processed ohlcv: {path}")
    source_symbol = payload.get("symbol")
    source_timeframe = payload.get("timeframe")
    if not _usable_identity(source_symbol) or not _usable_identity(source_timeframe):
        return SufficiencyResult(
            eligible=False,
            reason="missing_source_identity",
            closed_count=0,
            has_gaps=False,
            gap_timestamps=(),
            last_closed_timestamp=None,
            last_closed_age_ms=None,
            caught_up=is_caught_up(None, TIMEFRAME, now_ms),
            identity_checked=False,
        )
    try:
        candles = candles_from_payload(payload)
    except InvalidCacheError:
        raise
    except (KeyError, TypeError, ValueError) as exc:
        raise InvalidCacheError(f"invalid processed ohlcv: {path}") from exc
    return assess_4h_sufficiency(
        symbol,
        candles,
        now_ms=now_ms,
        source_symbol=source_symbol,
        source_timeframe=source_timeframe,
    )


def _usable_identity(value: Any) -> bool:
    return isinstance(value, str) and bool(value)

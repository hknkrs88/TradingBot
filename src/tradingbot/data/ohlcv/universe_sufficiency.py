"""Scan universe symbols with processed 4h sufficiency. Read-only; no sync or orders."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from tradingbot.data.io import InvalidCacheError
from tradingbot.data.ohlcv.processed_sufficiency import assess_processed_4h
from tradingbot.data.ohlcv.store import is_caught_up
from tradingbot.data.ohlcv.sufficiency import TIMEFRAME, SufficiencyResult

_CACHE_ERROR_PREFIXES: tuple[tuple[str, str], ...] = (
    ("missing cache:", "missing_processed_cache"),
    ("corrupt cache:", "corrupt_processed_cache"),
    ("ohlcv payload missing candles list", "invalid_processed_payload"),
    ("invalid processed ohlcv:", "invalid_processed_payload"),
    ("unsafe_promote_journal:", "unsafe_promote_journal"),
)


@dataclass(frozen=True)
class UniverseSymbolAssessment:
    symbol: str
    sufficiency: SufficiencyResult


@dataclass(frozen=True)
class UniverseSufficiencyReport:
    now_ms: int
    universe_path: Path
    assessments: tuple[UniverseSymbolAssessment, ...]
    eligible: tuple[UniverseSymbolAssessment, ...]
    rejected: tuple[UniverseSymbolAssessment, ...]


def assess_universe_4h(*, universe_path: Path, root: Path, now_ms: int) -> UniverseSufficiencyReport:
    """Assess each universe symbol's processed 4h JSON. Does not write."""
    from tradingbot.data.ohlcv.sync import load_universe_symbols

    symbols = load_universe_symbols(universe_path)
    seen: set[str] = set()
    assessments: list[UniverseSymbolAssessment] = []
    for symbol in symbols:
        if symbol in seen:
            assessments.append(
                UniverseSymbolAssessment(
                    symbol=symbol,
                    sufficiency=_synthetic("duplicate_universe_symbol", now_ms=now_ms),
                )
            )
            continue
        seen.add(symbol)
        try:
            sufficiency = assess_processed_4h(symbol, root=root, now_ms=now_ms)
        except InvalidCacheError as exc:
            sufficiency = _result_from_cache_error(exc, now_ms=now_ms)
        assessments.append(UniverseSymbolAssessment(symbol=symbol, sufficiency=sufficiency))
    rows = tuple(assessments)
    return UniverseSufficiencyReport(
        now_ms=now_ms,
        universe_path=universe_path,
        assessments=rows,
        eligible=tuple(item for item in rows if item.sufficiency.eligible is True),
        rejected=tuple(item for item in rows if item.sufficiency.eligible is not True),
    )


def _result_from_cache_error(exc: InvalidCacheError, *, now_ms: int) -> SufficiencyResult:
    message = str(exc)
    for prefix, reason in _CACHE_ERROR_PREFIXES:
        if message.startswith(prefix):
            return _synthetic(reason, now_ms=now_ms)
    raise


def _synthetic(reason: str, *, now_ms: int) -> SufficiencyResult:
    return SufficiencyResult(
        eligible=False,
        reason=reason,
        closed_count=0,
        has_gaps=False,
        gap_timestamps=(),
        last_closed_timestamp=None,
        last_closed_age_ms=None,
        caught_up=is_caught_up(None, TIMEFRAME, now_ms),
        identity_checked=False,
    )

"""Load closed 4h features for safe selection candidates. No score, rank, or orders."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from tradingbot.data.io import InvalidCacheError, load_json
from tradingbot.data.ohlcv.archive_promote import refuse_unsafe_promote_journal
from tradingbot.data.ohlcv.quality import is_incomplete
from tradingbot.data.ohlcv.selection_candidates import selection_candidates
from tradingbot.data.ohlcv.store import candles_from_payload, processed_path
from tradingbot.data.ohlcv.sufficiency import TIMEFRAME
from tradingbot.data.ohlcv.timeframes import timeframe_ms
from tradingbot.data.ohlcv.universe_sufficiency import UniverseSufficiencyReport
from tradingbot.selection.rank_features import RankFeatures4h, RankFeaturesError, rank_features_4h
from tradingbot.strategy.models import CandleSeries

_JOURNAL_PREFIX = "unsafe_promote_journal:"
_CACHE_ERROR_PREFIXES: tuple[tuple[str, str], ...] = (
    ("missing cache:", "missing_processed_cache"),
    ("corrupt cache:", "corrupt_processed_cache"),
    ("ohlcv payload missing candles list", "invalid_processed_payload"),
    ("invalid processed ohlcv:", "invalid_processed_payload"),
)
_FEATURE_ERROR_MARKERS: tuple[tuple[str, str], ...] = (
    ("missing_source_identity", "missing_source_identity"),
    ("symbol_mismatch", "symbol_mismatch"),
    ("timeframe_mismatch", "timeframe_mismatch"),
    ("need at least 50", "too_few_closed_candles"),
    ("gap in last 50", "gap_in_last_50_4h"),
    ("unaligned 4h timestamp", "unaligned_timestamp"),
    ("close must be positive", "non_positive_close"),
    ("negative volume", "negative_volume"),
    ("undefined ratio", "undefined_ratio"),
    ("non-finite rank feature", "non_finite_feature"),
    ("timeframe '4h'", "timeframe_mismatch"),
)


@dataclass(frozen=True)
class CandidateFeatures:
    symbol: str
    features: RankFeatures4h


@dataclass(frozen=True)
class CandidateFeatureRejection:
    symbol: str
    reason: str


@dataclass(frozen=True)
class CandidateFeaturesReport:
    now_ms: int
    features: tuple[CandidateFeatures, ...]
    rejected: tuple[CandidateFeatureRejection, ...]


def candidate_features_4h(
    report: UniverseSufficiencyReport, *, root: Path
) -> CandidateFeaturesReport:
    """Pair safe candidates with 4h features. Does not mutate report or write files."""
    symbols = selection_candidates(report)
    now_ms = report.now_ms
    tf_ms = timeframe_ms(TIMEFRAME)
    features: list[CandidateFeatures] = []
    rejected: list[CandidateFeatureRejection] = []
    for symbol in symbols:
        try:
            computed = _features_for_symbol(symbol, root=root, now_ms=now_ms, tf_ms=tf_ms)
        except InvalidCacheError as exc:
            if str(exc).startswith(_JOURNAL_PREFIX):
                raise
            rejected.append(
                CandidateFeatureRejection(symbol=symbol, reason=_cache_reason(exc))
            )
            continue
        except RankFeaturesError as exc:
            rejected.append(
                CandidateFeatureRejection(symbol=symbol, reason=_feature_reason(exc))
            )
            continue
        features.append(CandidateFeatures(symbol=symbol, features=computed))
    return CandidateFeaturesReport(
        now_ms=now_ms,
        features=tuple(features),
        rejected=tuple(rejected),
    )


def _features_for_symbol(
    symbol: str, *, root: Path, now_ms: int, tf_ms: int
) -> RankFeatures4h:
    refuse_unsafe_promote_journal(root, symbol, TIMEFRAME)
    path = processed_path(Path(root) / "data" / "processed" / "ohlcv", symbol, TIMEFRAME)
    payload = load_json(path)
    if not isinstance(payload, dict):
        raise InvalidCacheError(f"invalid processed ohlcv: {path}")
    source_symbol = payload.get("symbol")
    source_timeframe = payload.get("timeframe")
    if not _usable_identity(source_symbol) or not _usable_identity(source_timeframe):
        raise RankFeaturesError("missing_source_identity")
    if source_symbol != symbol:
        raise RankFeaturesError("symbol_mismatch")
    if source_timeframe != TIMEFRAME:
        raise RankFeaturesError("timeframe_mismatch")
    try:
        candles = candles_from_payload(payload)
    except InvalidCacheError:
        raise
    except (KeyError, TypeError, ValueError) as exc:
        raise InvalidCacheError(f"invalid processed ohlcv: {path}") from exc
    closed = [item for item in candles if not is_incomplete(item, tf_ms, now_ms)]
    series = CandleSeries.from_closed(
        symbol=symbol,
        timeframe=TIMEFRAME,
        candles=closed,
        now_ms=now_ms,
    )
    return rank_features_4h(series)


def _usable_identity(value: Any) -> bool:
    return isinstance(value, str) and bool(value)


def _cache_reason(exc: InvalidCacheError) -> str:
    message = str(exc)
    for prefix, reason in _CACHE_ERROR_PREFIXES:
        if message.startswith(prefix):
            return reason
    raise exc


def _feature_reason(exc: RankFeaturesError) -> str:
    message = str(exc)
    for marker, reason in _FEATURE_ERROR_MARKERS:
        if marker in message:
            return reason
    return message

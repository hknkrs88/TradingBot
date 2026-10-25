"""4h validation dataset gate and manifest-only rebuild. No network or orders.

An initial dataset has no ``manifest_4h.json`` and no processed 4h file for any
selected symbol. A continuing dataset is anything else and must match the
manifest before a validation sync may write. Rebuild ignores a stale or broken
manifest and writes a new one only after every selected symbol file loads.
"""

from __future__ import annotations

from pathlib import Path

from tradingbot.data.io import InvalidCacheError, load_json, sha256_file
from tradingbot.data.ohlcv.manifest import (
    _relative_posix,
    build_ohlcv_manifest,
    write_ohlcv_manifest,
)
from tradingbot.data.ohlcv.quality import require_aligned_4h, sanitize_candles
from tradingbot.data.ohlcv.store import load_candles, processed_path
from tradingbot.data.ohlcv.sync import load_universe_symbols

TIMEFRAME = "4h"


class ValidationDatasetError(Exception):
    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


def _series_file(processed_root: Path, symbol: str) -> Path:
    return processed_path(processed_root, symbol, TIMEFRAME)


def _cleaned(path: Path, *, symbol: str, now_ms: int, drop_incomplete: bool):
    candles = load_candles(path)
    require_aligned_4h(symbol, candles)
    cleaned, _issues = sanitize_candles(
        candles,
        TIMEFRAME,
        now_ms=now_ms,
        drop_incomplete=drop_incomplete,
    )
    return cleaned


def inspect_4h_validation_dataset(
    *,
    root: Path,
    universe_path: Path,
    processed_root: Path,
    now_ms: int,
    drop_incomplete: bool,
) -> str:
    """Return ``initial`` or ``ready``. Raise before any candle write is allowed."""
    symbols = load_universe_symbols(universe_path)
    files = [_series_file(processed_root, symbol) for symbol in symbols]
    manifest_path = processed_root / "manifest_4h.json"
    if not manifest_path.is_file() and not any(path.is_file() for path in files):
        return "initial"
    if not manifest_path.is_file():
        raise ValidationDatasetError("manifest_missing")
    try:
        manifest = load_json(manifest_path)
    except InvalidCacheError as exc:
        raise ValidationDatasetError("manifest_unreadable") from exc
    if not isinstance(manifest, dict):
        raise ValidationDatasetError("manifest_unreadable")

    universe = manifest.get("universe")
    if not isinstance(universe, dict):
        raise ValidationDatasetError("universe_hash_mismatch")
    if universe.get("sha256") != sha256_file(universe_path):
        raise ValidationDatasetError("universe_hash_mismatch")
    if universe.get("path") != _relative_posix(universe_path, root):
        raise ValidationDatasetError("universe_path_mismatch")
    if universe.get("symbols") != symbols or universe.get("symbol_count") != len(symbols):
        raise ValidationDatasetError("universe_symbols_mismatch")
    if manifest.get("incomplete_symbols"):
        raise ValidationDatasetError("incomplete_manifest")

    rows = manifest.get("series")
    if not isinstance(rows, list):
        raise ValidationDatasetError("manifest_unreadable")
    by_symbol = {}
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("symbol"), str):
            raise ValidationDatasetError("manifest_unreadable")
        if row["symbol"] in by_symbol:
            raise ValidationDatasetError("manifest_unreadable")
        by_symbol[row["symbol"]] = row

    for symbol, path in zip(symbols, files, strict=True):
        if not path.is_file():
            raise ValidationDatasetError(f"missing_file:{symbol}")
        row = by_symbol.get(symbol)
        if row is None:
            raise ValidationDatasetError(f"missing_manifest_row:{symbol}")
        try:
            cleaned = _cleaned(
                path,
                symbol=symbol,
                now_ms=now_ms,
                drop_incomplete=drop_incomplete,
            )
        except InvalidCacheError as exc:
            raise ValidationDatasetError(f"unreadable_file:{symbol}") from exc
        if not cleaned:
            raise ValidationDatasetError(f"empty_series:{symbol}")
        if row.get("sha256") != sha256_file(path):
            raise ValidationDatasetError(f"hash_mismatch:{symbol}")
        if row.get("range_start") != cleaned[0].timestamp or row.get("range_end") != cleaned[-1].timestamp:
            raise ValidationDatasetError(f"range_mismatch:{symbol}")
        if row.get("candle_count") != len(cleaned):
            raise ValidationDatasetError(f"count_mismatch:{symbol}")
    return "ready"


def rebuild_4h_manifest(
    *,
    root: Path,
    universe_path: Path,
    processed_root: Path,
    provider: str,
    candle_limit: int,
    drop_incomplete: bool,
    now_ms: int,
    generated_at: str | None = None,
) -> Path:
    """Rewrite ``manifest_4h.json`` from disk. Candle files are only read."""
    symbols = load_universe_symbols(universe_path)
    paths: list[Path] = []
    for symbol in symbols:
        path = _series_file(processed_root, symbol)
        if not path.is_file():
            raise ValidationDatasetError(f"missing_file:{symbol}")
        try:
            cleaned = _cleaned(
                path,
                symbol=symbol,
                now_ms=now_ms,
                drop_incomplete=drop_incomplete,
            )
        except InvalidCacheError as exc:
            raise ValidationDatasetError(f"unreadable_file:{symbol}") from exc
        if not cleaned:
            raise ValidationDatasetError(f"empty_series:{symbol}")
        paths.append(path)

    before = {path: path.read_bytes() for path in paths}
    manifest = build_ohlcv_manifest(
        universe_path=universe_path,
        processed_root=processed_root,
        timeframe=TIMEFRAME,
        provider=provider,
        candle_limit=candle_limit,
        drop_incomplete=drop_incomplete,
        now_ms=now_ms,
        root=root,
        generated_at=generated_at,
    )
    if manifest["incomplete_symbols"] or len(manifest["series"]) != len(symbols):
        raise ValidationDatasetError("incomplete_manifest")
    for path, payload in before.items():
        if path.read_bytes() != payload:
            raise ValidationDatasetError("candle_bytes_changed")
    written = write_ohlcv_manifest(processed_root, manifest)
    for path, payload in before.items():
        if path.read_bytes() != payload:
            raise ValidationDatasetError("candle_bytes_changed")
    return written

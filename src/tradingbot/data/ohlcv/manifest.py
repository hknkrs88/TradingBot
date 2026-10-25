"""Dataset identity for processed OHLCV. No network, no provider calls."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from tradingbot.data.io import InvalidCacheError, atomic_write_json, sha256_file
from tradingbot.data.ohlcv.quality import require_aligned_4h, sanitize_candles
from tradingbot.data.ohlcv.store import load_candles, processed_path
from tradingbot.data.ohlcv.timeframes import UnsupportedTimeframeError, timeframe_ms

log = logging.getLogger("tradingbot.data.ohlcv.manifest")

MANIFEST_SCHEMA = "ohlcv_dataset_manifest/v1"


def _relative_posix(path: Path, root: Path) -> str:
    resolved = path.resolve()
    try:
        return resolved.relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def _series_relpath(processed_root: Path, symbol: str, timeframe: str) -> str:
    return processed_path(processed_root, symbol, timeframe).relative_to(
        processed_root
    ).as_posix()


def _universe_symbols(universe_path: Path) -> list[str]:
    from tradingbot.data.ohlcv.sync import load_universe_symbols

    return load_universe_symbols(universe_path)


def build_ohlcv_manifest(
    *,
    universe_path: Path,
    processed_root: Path,
    timeframe: str,
    provider: str,
    candle_limit: int,
    drop_incomplete: bool,
    now_ms: int,
    root: Path,
    generated_at: str | None = None,
) -> dict[str, Any]:
    symbols = _universe_symbols(universe_path)
    series: list[dict[str, Any]] = []
    incomplete: list[str] = []
    try:
        timeframe_ms(timeframe)
        tf_ok = True
    except UnsupportedTimeframeError as exc:
        log.warning("ohlcv manifest skip timeframe=%s error=%s", timeframe, exc)
        tf_ok = False

    if not tf_ok:
        incomplete = list(symbols)
    else:
        for symbol in symbols:
            path = processed_path(processed_root, symbol, timeframe)
            try:
                candles = load_candles(path)
                if timeframe == "4h":
                    require_aligned_4h(symbol, candles)
                cleaned, _issues = sanitize_candles(
                    candles,
                    timeframe,
                    now_ms=now_ms,
                    drop_incomplete=drop_incomplete,
                )
            except (InvalidCacheError, KeyError, TypeError, ValueError) as exc:
                log.warning(
                    "ohlcv manifest skip symbol=%s tf=%s error=%s",
                    symbol,
                    timeframe,
                    exc,
                )
                incomplete.append(symbol)
                continue
            if not cleaned:
                incomplete.append(symbol)
                continue
            series.append(
                {
                    "symbol": symbol,
                    "path": _series_relpath(processed_root, symbol, timeframe),
                    "range_start": cleaned[0].timestamp,
                    "range_end": cleaned[-1].timestamp,
                    "candle_count": len(cleaned),
                    "sha256": sha256_file(path),
                }
            )

    stamp = generated_at or datetime.now(UTC).replace(microsecond=0).isoformat()
    return {
        "schema": MANIFEST_SCHEMA,
        "generated_at": stamp,
        "provider": provider,
        "timeframe": timeframe,
        "universe": {
            "path": _relative_posix(universe_path, root),
            "sha256": sha256_file(universe_path),
            "symbol_count": len(symbols),
            "symbols": list(symbols),
        },
        "config": {
            "provider": provider,
            "candle_limit": candle_limit,
            "drop_incomplete": drop_incomplete,
        },
        "series": series,
        "incomplete_symbols": incomplete,
    }


def write_ohlcv_manifest(processed_root: Path, manifest: dict[str, Any]) -> Path:
    timeframe = str(manifest["timeframe"])
    path = processed_root / f"manifest_{timeframe}.json"
    atomic_write_json(path, manifest)
    return path


def write_ohlcv_manifests(
    *,
    universe_path: Path,
    processed_root: Path,
    timeframes: tuple[str, ...],
    provider: str,
    candle_limit: int,
    drop_incomplete: bool,
    now_ms: int,
    root: Path,
) -> list[Path]:
    written: list[Path] = []
    for timeframe in timeframes:
        manifest = build_ohlcv_manifest(
            universe_path=universe_path,
            processed_root=processed_root,
            timeframe=timeframe,
            provider=provider,
            candle_limit=candle_limit,
            drop_incomplete=drop_incomplete,
            now_ms=now_ms,
            root=root,
        )
        written.append(write_ohlcv_manifest(processed_root, manifest))
    return written

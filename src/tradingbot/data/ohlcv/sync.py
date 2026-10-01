from __future__ import annotations

import logging
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import ccxt

from tradingbot.data.io import InvalidCacheError, atomic_write_json, load_json
from tradingbot.data.ohlcv.binance_ohlcv import BinancePublicOHLCVProvider
from tradingbot.data.ohlcv.models import Candle
from tradingbot.data.ohlcv.protocol import OHLCVProvider
from tradingbot.data.ohlcv.retry import call_with_retry
from tradingbot.data.ohlcv.store import (
    dump_candles,
    is_caught_up,
    last_closed_timestamp,
    load_candles,
    merge_candles,
    next_since,
    processed_path,
    raw_path,
)
from tradingbot.data.ohlcv.timeframes import UnsupportedTimeframeError, validate_timeframes

log = logging.getLogger("tradingbot.data.ohlcv.sync")

RETRYABLE = (
    ccxt.RateLimitExceeded,
    ccxt.NetworkError,
    ccxt.ExchangeNotAvailable,
    ccxt.RequestTimeout,
    TimeoutError,
    ConnectionError,
    OSError,
)


@dataclass(frozen=True)
class OHLCVConfig:
    provider: str
    timeframes: tuple[str, ...]
    candle_limit: int
    raw_dir: str
    processed_dir: str
    retry_max: int
    backoff_seconds: float
    drop_incomplete: bool


def load_ohlcv_config(path: Path) -> OHLCVConfig:
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    timeframes = validate_timeframes([str(item) for item in data.get("timeframes", [])])
    candle_limit = int(data.get("candle_limit", 1000))
    if candle_limit < 1 or candle_limit > 1000:
        raise ValueError("candle_limit must be between 1 and 1000")
    provider = str(data.get("provider", "binance_public"))
    if provider != "binance_public":
        raise ValueError(f"unsupported ohlcv provider {provider!r}")
    return OHLCVConfig(
        provider=provider,
        timeframes=timeframes,
        candle_limit=candle_limit,
        raw_dir=str(data.get("raw_dir", "data/raw/ohlcv")),
        processed_dir=str(data.get("processed_dir", "data/processed/ohlcv")),
        retry_max=int(data.get("retry_max", 3)),
        backoff_seconds=float(data.get("backoff_seconds", 1.0)),
        drop_incomplete=bool(data.get("drop_incomplete", True)),
    )


def load_universe_symbols(universe_path: Path) -> list[str]:
    payload = load_json(universe_path)
    rows = payload.get("universe") if isinstance(payload, dict) else None
    if not isinstance(rows, list) or not rows:
        raise InvalidCacheError(f"universe missing symbols: {universe_path}")
    symbols: list[str] = []
    for row in rows:
        symbol = str(row.get("binance_symbol") or "")
        if symbol:
            symbols.append(symbol)
    if not symbols:
        raise InvalidCacheError(f"universe has no binance_symbol values: {universe_path}")
    return symbols


def _empty_report() -> dict[str, Any]:
    return {
        "symbols_ok": 0,
        "symbols_failed": 0,
        "candles_fetched": 0,
        "cache_hits": 0,
        "cache_misses": 0,
        "series": [],
        "errors": [],
    }


def sync_symbol_timeframe(
    *,
    symbol: str,
    timeframe: str,
    provider: OHLCVProvider,
    config: OHLCVConfig,
    raw_root: Path,
    processed_root: Path,
    now_ms: int,
    sleep_fn: Callable[[float], None],
) -> dict[str, Any]:
    raw_file = raw_path(raw_root, config.provider, symbol, timeframe)
    processed_file = processed_path(processed_root, symbol, timeframe)

    existing: list[Candle] = []
    try:
        existing = load_candles(processed_file)
    except InvalidCacheError:
        existing = []

    last_closed = last_closed_timestamp(existing, timeframe, now_ms)
    if is_caught_up(last_closed, timeframe, now_ms):
        cleaned, issues = merge_candles(
            existing,
            [],
            timeframe,
            now_ms=now_ms,
            drop_incomplete=config.drop_incomplete,
        )
        if cleaned != existing:
            dump_candles(processed_file, cleaned, symbol=symbol, timeframe=timeframe)
        range_start = cleaned[0].timestamp if cleaned else None
        range_end = last_closed_timestamp(cleaned, timeframe, now_ms)
        return {
            "symbol": symbol,
            "timeframe": timeframe,
            "reason": "cache_hit",
            "range_start": range_start,
            "range_end": range_end,
            "candles_fetched": 0,
            "issues": issues,
        }

    since = next_since(last_closed, timeframe) if last_closed is not None else None

    def _fetch() -> list[Candle]:
        return provider.fetch_ohlcv(
            symbol, timeframe, since=since, limit=config.candle_limit
        )

    fetched = call_with_retry(
        _fetch,
        retry_max=config.retry_max,
        backoff_seconds=config.backoff_seconds,
        sleep_fn=sleep_fn,
        retry_exceptions=RETRYABLE,
    )
    dump_candles(
        raw_file,
        fetched,
        symbol=symbol,
        timeframe=timeframe,
        extra={"since": since, "limit": config.candle_limit},
    )
    merged, issues = merge_candles(
        existing,
        fetched,
        timeframe,
        now_ms=now_ms,
        drop_incomplete=config.drop_incomplete,
    )
    dump_candles(processed_file, merged, symbol=symbol, timeframe=timeframe)
    return {
        "symbol": symbol,
        "timeframe": timeframe,
        "reason": "fetched",
        "range_start": merged[0].timestamp if merged else None,
        "range_end": last_closed_timestamp(merged, timeframe, now_ms),
        "candles_fetched": len(fetched),
        "issues": issues,
        "since": since,
    }


def sync_ohlcv(
    *,
    universe_path: Path,
    config: OHLCVConfig,
    provider: OHLCVProvider | None = None,
    root: Path | None = None,
    now_ms: int,
    sleep_fn: Callable[[float], None] | None = None,
) -> dict[str, Any]:
    base = root or Path(".")
    raw_root = base / config.raw_dir
    processed_root = base / config.processed_dir
    client = provider or BinancePublicOHLCVProvider()
    sleeper = sleep_fn or __import__("time").sleep
    symbols = load_universe_symbols(universe_path)
    report = _empty_report()
    failed_symbols: set[str] = set()
    ok_symbols: set[str] = set()

    for symbol in symbols:
        symbol_failed = False
        for timeframe in config.timeframes:
            try:
                series = sync_symbol_timeframe(
                    symbol=symbol,
                    timeframe=timeframe,
                    provider=client,
                    config=config,
                    raw_root=raw_root,
                    processed_root=processed_root,
                    now_ms=now_ms,
                    sleep_fn=sleeper,
                )
            except UnsupportedTimeframeError as exc:
                symbol_failed = True
                report["errors"].append(
                    {
                        "symbol": symbol,
                        "timeframe": timeframe,
                        "reason": "unsupported_timeframe",
                        "message": str(exc),
                    }
                )
                report["series"].append(
                    {
                        "symbol": symbol,
                        "timeframe": timeframe,
                        "reason": "failed",
                        "range_start": None,
                        "range_end": None,
                        "issues": [],
                    }
                )
                continue
            except Exception as exc:
                symbol_failed = True
                log.warning("ohlcv sync failed symbol=%s tf=%s error=%s", symbol, timeframe, exc)
                report["errors"].append(
                    {
                        "symbol": symbol,
                        "timeframe": timeframe,
                        "reason": type(exc).__name__,
                        "message": str(exc),
                    }
                )
                report["series"].append(
                    {
                        "symbol": symbol,
                        "timeframe": timeframe,
                        "reason": "failed",
                        "range_start": None,
                        "range_end": None,
                        "issues": [],
                    }
                )
                continue

            report["series"].append(
                {
                    "symbol": series["symbol"],
                    "timeframe": series["timeframe"],
                    "reason": series["reason"],
                    "range_start": series["range_start"],
                    "range_end": series["range_end"],
                    "issues": list(series.get("issues") or []),
                }
            )
            if series["reason"] == "cache_hit":
                report["cache_hits"] += 1
            else:
                report["cache_misses"] += 1
                report["candles_fetched"] += int(series["candles_fetched"])
        if symbol_failed:
            failed_symbols.add(symbol)
        else:
            ok_symbols.add(symbol)

    report["symbols_ok"] = len(ok_symbols)
    report["symbols_failed"] = len(failed_symbols)
    atomic_write_json(processed_root / "sync_report.json", report)
    return report

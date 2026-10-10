"""Load a pinned OHLCV snapshot for backtest. No strategy, no simulation, no network."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from tradingbot.data.io import InvalidCacheError, load_json, sha256_file
from tradingbot.data.ohlcv.reader import load_processed
from tradingbot.data.ohlcv.store import processed_path
from tradingbot.data.ohlcv.timeframes import timeframe_ms
from tradingbot.strategy.models import CandleSeries

MANIFEST_SCHEMA = "ohlcv_dataset_manifest/v1"
PROCESSED_DIR = Path("data") / "processed" / "ohlcv"


@dataclass(frozen=True)
class BacktestDataset:
    series: CandleSeries
    manifest_path: str
    timeframe: str
    universe_sha256: str
    processed_sha256: str
    processed_relpath: str
    bar_count: int


def _posix(path: Path) -> str:
    return path.as_posix()


def _timeframe_from_filename(manifest_path: Path) -> str | None:
    name = manifest_path.name
    prefix = "manifest_"
    suffix = ".json"
    if not (name.startswith(prefix) and name.endswith(suffix)):
        return None
    token = name[len(prefix) : -len(suffix)]
    return token or None


def _require_file(path: Path, *, kind: str) -> None:
    if not path.is_file():
        raise InvalidCacheError(f"missing {kind}: {path}")


def _series_rows_for_symbol(rows: Any, symbol: str) -> list[dict[str, Any]]:
    if not isinstance(rows, list):
        raise ValueError("manifest series must be a list")
    matched: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("manifest series row must be an object")
        if row.get("symbol") == symbol:
            matched.append(row)
    return matched


def _resolved_processed_file(processed_root: Path, relpath: str) -> Path:
    relative = Path(relpath)
    if relative.is_absolute():
        raise ValueError("processed path must be relative")
    if ".." in relative.parts:
        raise ValueError("processed path escapes processed root")
    candidate = (processed_root / relative).resolve()
    try:
        candidate.relative_to(processed_root.resolve())
    except ValueError as exc:
        raise ValueError("processed path escapes processed root") from exc
    return candidate


def load_backtest_series(
    manifest_path: Path | str,
    symbol: str,
    *,
    root: Path | str,
) -> BacktestDataset:
    given_manifest = Path(manifest_path)
    root_path = Path(root)
    manifest_file = given_manifest if given_manifest.is_absolute() else root_path / given_manifest
    payload = load_json(manifest_file)

    if not isinstance(payload, dict):
        raise ValueError("manifest must be an object")
    if payload.get("schema") != MANIFEST_SCHEMA:
        raise ValueError("unsupported manifest schema")
    timeframe = payload.get("timeframe")
    if not timeframe:
        raise ValueError("missing timeframe")
    timeframe = str(timeframe)
    timeframe_ms(timeframe)
    from_name = _timeframe_from_filename(given_manifest)
    if from_name is not None and from_name != timeframe:
        raise ValueError("manifest filename timeframe mismatch")
    from tradingbot.data.ohlcv.archive_promote import refuse_unsafe_promote_journal

    refuse_unsafe_promote_journal(root_path, symbol, timeframe)

    universe = payload.get("universe")
    if not isinstance(universe, dict):
        raise ValueError("manifest universe must be an object")
    symbols = universe.get("symbols")
    if not isinstance(symbols, list):
        raise ValueError("manifest universe.symbols must be a list")
    if symbol not in symbols:
        raise ValueError(f"symbol {symbol!r} not in universe")
    incomplete = payload.get("incomplete_symbols")
    if not isinstance(incomplete, list):
        raise ValueError("manifest incomplete_symbols must be a list")
    if symbol in incomplete:
        raise ValueError(f"symbol {symbol!r} is incomplete")

    matched = _series_rows_for_symbol(payload.get("series"), symbol)
    if not matched:
        raise ValueError(f"no series record for {symbol!r}")
    if len(matched) > 1:
        raise ValueError(f"duplicate series record for {symbol!r}")
    row = matched[0]
    relpath = row.get("path")
    expected_sha = row.get("sha256")
    if not isinstance(relpath, str) or not relpath:
        raise ValueError("series path missing")
    if not isinstance(expected_sha, str) or not expected_sha:
        raise ValueError("series sha256 missing")

    universe_rel = universe.get("path")
    universe_sha = universe.get("sha256")
    if not isinstance(universe_rel, str) or not universe_rel:
        raise ValueError("universe path missing")
    if not isinstance(universe_sha, str) or not universe_sha:
        raise ValueError("universe sha256 missing")
    universe_file = Path(universe_rel) if Path(universe_rel).is_absolute() else root_path / universe_rel
    if Path(universe_rel).is_absolute():
        raise ValueError("universe path must be relative")
    _require_file(universe_file, kind="universe")
    actual_universe_sha = sha256_file(universe_file)
    if actual_universe_sha != universe_sha:
        raise ValueError("universe hash mismatch")

    processed_root = root_path / PROCESSED_DIR
    processed_file = _resolved_processed_file(processed_root, relpath)
    expected_file = processed_path(processed_root, symbol, timeframe).resolve()
    if processed_file != expected_file:
        raise ValueError("processed path does not match reader layout")
    _require_file(processed_file, kind="processed ohlcv")
    actual_processed_sha = sha256_file(processed_file)
    if actual_processed_sha != expected_sha:
        raise ValueError("processed hash mismatch")

    candles = load_processed(symbol, timeframe, root=root_path)
    if not candles:
        raise ValueError("empty processed dataset")

    series = CandleSeries.from_closed(
        symbol=symbol,
        timeframe=timeframe,
        candles=candles,
        now_ms=candles[-1].timestamp + timeframe_ms(timeframe),
    )
    if row.get("range_start") != series.candles[0].timestamp:
        raise ValueError("range_start mismatch")
    if row.get("range_end") != series.candles[-1].timestamp:
        raise ValueError("range_end mismatch")
    if row.get("candle_count") != len(series.candles):
        raise ValueError("candle_count mismatch")

    return BacktestDataset(
        series=series,
        manifest_path=_posix(given_manifest),
        timeframe=timeframe,
        universe_sha256=universe_sha,
        processed_sha256=expected_sha,
        processed_relpath=relpath,
        bar_count=len(series.candles),
    )

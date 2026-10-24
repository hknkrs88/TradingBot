"""Join research indicators to forward returns. Does not score or trade."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from tradingbot.data.ohlcv.quality import is_incomplete
from tradingbot.data.ohlcv.reader import load_processed
from tradingbot.data.ohlcv.store import processed_path
from tradingbot.data.ohlcv.sync import load_universe_symbols
from tradingbot.data.ohlcv.timeframes import timeframe_ms
from tradingbot.indicators import adx, atr, macd, roc, rsi, sma
from tradingbot.research.forward import HORIZONS, forward_returns
from tradingbot.research.spearman import SpearmanResult, spearman_corr
from tradingbot.strategy.models import CandleSeries

FEATURE_NAMES: tuple[str, ...] = (
    "close_vs_sma50_pct",
    "ma_spread_pct",
    "roc6_pct",
    "rsi14",
    "macd_histogram",
    "adx14",
    "atr_over_close",
)
CROSS_SECTION_MIN_SYMBOLS = 10
TIMEFRAME = "4h"


@dataclass(frozen=True)
class FeaturePoint:
    timestamp: int
    value: float


def feature_series(series: CandleSeries) -> dict[str, tuple[FeaturePoint, ...]]:
    """Build named features from existing indicator functions. No new formulas."""
    closes = {candle.timestamp: candle.close for candle in series.candles}
    sma20 = {p.timestamp: p.value for p in sma(series, 20).points if p.value is not None}
    sma50 = {p.timestamp: p.value for p in sma(series, 50).points if p.value is not None}
    roc6 = {p.timestamp: p.value for p in roc(series, 6).points if p.value is not None}
    rsi14 = {p.timestamp: p.value for p in rsi(series, 14).points if p.value is not None}
    hist = {p.timestamp: p.value for p in macd(series).histogram.points if p.value is not None}
    adx14 = {p.timestamp: p.value for p in adx(series, 14).adx.points if p.value is not None}
    atr14 = {p.timestamp: p.value for p in atr(series, 14).points if p.value is not None}

    close_vs: list[FeaturePoint] = []
    spread: list[FeaturePoint] = []
    for ts, slow in sma50.items():
        close = closes.get(ts)
        if close is None or close <= 0.0 or slow == 0.0:
            continue
        close_vs.append(FeaturePoint(ts, (close / slow - 1.0) * 100.0))
        fast = sma20.get(ts)
        if fast is None:
            continue
        spread.append(FeaturePoint(ts, (fast / slow - 1.0) * 100.0))

    atr_ratio: list[FeaturePoint] = []
    for ts, value in atr14.items():
        close = closes.get(ts)
        if close is None or close <= 0.0:
            continue
        atr_ratio.append(FeaturePoint(ts, value / close))

    return {
        "close_vs_sma50_pct": tuple(close_vs),
        "ma_spread_pct": tuple(spread),
        "roc6_pct": tuple(FeaturePoint(ts, value * 100.0) for ts, value in roc6.items()),
        "rsi14": tuple(FeaturePoint(ts, value) for ts, value in rsi14.items()),
        "macd_histogram": tuple(FeaturePoint(ts, value) for ts, value in hist.items()),
        "adx14": tuple(FeaturePoint(ts, value) for ts, value in adx14.items()),
        "atr_over_close": tuple(atr_ratio),
    }


def paired_points(
    features: Sequence[FeaturePoint],
    returns: Sequence[tuple[int, float]],
) -> tuple[tuple[int, float, float], ...]:
    """Time-sorted (timestamp, feature, forward_return) inner join. No fill."""
    by_ts = {ts: value for ts, value in returns}
    return tuple(
        (point.timestamp, point.value, by_ts[point.timestamp])
        for point in features
        if point.timestamp in by_ts
    )


def paired_sample(
    features: Sequence[FeaturePoint],
    returns: Sequence[tuple[int, float]],
) -> tuple[list[float], list[float]]:
    points = paired_points(features, returns)
    return [item[1] for item in points], [item[2] for item in points]


def correlate_features(
    series: CandleSeries,
    horizons: Sequence[int] = HORIZONS,
) -> dict[str, dict[int, SpearmanResult]]:
    features = feature_series(series)
    fwds = forward_returns(series, horizons)
    out: dict[str, dict[int, SpearmanResult]] = {}
    for name in FEATURE_NAMES:
        out[name] = {}
        for horizon in horizons:
            xs, ys = paired_sample(features[name], fwds[horizon])
            out[name][horizon] = spearman_corr(xs, ys)
    return out


def load_closed_series(symbol: str, *, root: Path, now_ms: int) -> CandleSeries:
    tf_ms = timeframe_ms(TIMEFRAME)
    candles = load_processed(symbol, TIMEFRAME, root=root)
    closed = tuple(item for item in candles if not is_incomplete(item, tf_ms, now_ms))
    return CandleSeries.from_closed(
        symbol=symbol,
        timeframe=TIMEFRAME,
        candles=closed,
        now_ms=now_ms,
    )


def run_universe_study(
    *,
    root: Path,
    now_ms: int,
    universe_path: Path | None = None,
    horizons: Sequence[int] = HORIZONS,
    cross_section_min_symbols: int = CROSS_SECTION_MIN_SYMBOLS,
) -> dict:
    """Read local 4h files and correlate features with forward returns. No sync."""
    universe_path = universe_path or (root / "data" / "processed" / "universe.json")
    symbols = load_universe_symbols(universe_path)
    processed_root = root / "data" / "processed" / "ohlcv"
    per_symbol: list[dict] = []
    pooled: dict[str, dict[int, tuple[list[float], list[float]]]] = {
        name: {h: ([], []) for h in horizons} for name in FEATURE_NAMES
    }
    cs_rows: dict[str, dict[int, dict[int, list[tuple[str, float, float]]]]] = {
        name: {h: defaultdict(list) for h in horizons} for name in FEATURE_NAMES
    }
    missing: list[str] = []
    errors: list[dict] = []

    for symbol in symbols:
        path = processed_path(processed_root, symbol, TIMEFRAME)
        if not path.exists():
            missing.append(symbol)
            continue
        try:
            series = load_closed_series(symbol, root=root, now_ms=now_ms)
        except Exception as exc:
            errors.append({"symbol": symbol, "error": f"{type(exc).__name__}: {exc}"})
            continue
        features = feature_series(series)
        fwds = forward_returns(series, horizons)
        rows: dict[str, dict[int, dict]] = {}
        for name in FEATURE_NAMES:
            rows[name] = {}
            for horizon in horizons:
                xs, ys = paired_sample(features[name], fwds[horizon])
                result = spearman_corr(xs, ys)
                rows[name][horizon] = _result_dict(result)
                pooled[name][horizon][0].extend(xs)
                pooled[name][horizon][1].extend(ys)
                by_ts = {ts: value for ts, value in fwds[horizon]}
                for point in features[name]:
                    fwd = by_ts.get(point.timestamp)
                    if fwd is None:
                        continue
                    cs_rows[name][horizon][point.timestamp].append((symbol, point.value, fwd))
        per_symbol.append(
            {
                "symbol": symbol,
                "closed_candles": len(series.candles),
                "first_ts": series.candles[0].timestamp if series.candles else None,
                "last_ts": series.candles[-1].timestamp if series.candles else None,
                "correlations": rows,
            }
        )

    pooled_out = {
        name: {str(h): _result_dict(spearman_corr(*pooled[name][h])) for h in horizons}
        for name in FEATURE_NAMES
    }
    cs_out = {
        name: {
            str(h): _cross_section_summary(cs_rows[name][h], cross_section_min_symbols)
            for h in horizons
        }
        for name in FEATURE_NAMES
    }
    rho_lists = {
        name: {
            str(h): [
                row["correlations"][name][h]["rho"]
                for row in per_symbol
                if row["correlations"][name][h]["rho"] is not None
            ]
            for h in horizons
        }
        for name in FEATURE_NAMES
    }
    return {
        "timeframe": TIMEFRAME,
        "now_ms": now_ms,
        "universe_path": universe_path.as_posix(),
        "universe_symbol_count": len(symbols),
        "analyzed_symbols": len(per_symbol),
        "missing_files": missing,
        "load_errors": errors,
        "horizons": list(horizons),
        "features": list(FEATURE_NAMES),
        "cross_section_min_symbols": cross_section_min_symbols,
        "cross_section_definition": (
            f"At each timestamp, Spearman uses symbols that have both the feature "
            f"and the horizon return; timestamps with fewer than "
            f"{cross_section_min_symbols} such symbols are dropped. The reported "
            f"value is the median of those per-timestamp coefficients."
        ),
        "pooled_mixes_symbols_and_market": True,
        "survivorship_listing_lag": True,
        "quote_volume_used": False,
        "pooled": pooled_out,
        "cross_section": cs_out,
        "per_symbol_rho_distribution": {
            name: {
                horizon: _dist(values) for horizon, values in by_h.items()
            }
            for name, by_h in rho_lists.items()
        },
        "per_symbol": per_symbol,
    }


def _result_dict(result: SpearmanResult) -> dict:
    return {"n": result.n, "rho": result.rho, "reason": result.reason}


def _dist(values: list[float]) -> dict:
    if not values:
        return {"count": 0, "min": None, "p50": None, "max": None}
    ordered = sorted(values)
    mid = ordered[len(ordered) // 2] if len(ordered) % 2 else (
        ordered[len(ordered) // 2 - 1] + ordered[len(ordered) // 2]
    ) / 2.0
    return {"count": len(ordered), "min": ordered[0], "p50": mid, "max": ordered[-1]}


def _cross_section_summary(
    by_ts: Mapping[int, list[tuple[str, float, float]]],
    min_symbols: int,
) -> dict:
    rhos: list[float] = []
    ns: list[int] = []
    skipped_short = 0
    undefined = 0
    for _ts, rows in by_ts.items():
        if len(rows) < min_symbols:
            skipped_short += 1
            continue
        xs = [item[1] for item in rows]
        ys = [item[2] for item in rows]
        result = spearman_corr(xs, ys)
        ns.append(result.n)
        if result.rho is None:
            undefined += 1
            continue
        rhos.append(result.rho)
    return {
        "timestamps_with_min_symbols": len(rhos) + undefined,
        "timestamps_skipped_short": skipped_short,
        "timestamps_undefined": undefined,
        "median_n_symbols": (sorted(ns)[len(ns) // 2] if ns else None),
        "rho_count": len(rhos),
        "rho_min": min(rhos) if rhos else None,
        "rho_p50": (sorted(rhos)[len(rhos) // 2] if rhos else None),
        "rho_max": max(rhos) if rhos else None,
    }

"""G-O robustness slices of G-N pairs. Does not change formulas or G-N output."""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from statistics import pstdev

from tradingbot.data.ohlcv.store import processed_path
from tradingbot.data.ohlcv.sync import load_universe_symbols
from tradingbot.research.forward import HORIZONS, forward_returns
from tradingbot.research.indicator_assoc import (
    CROSS_SECTION_MIN_SYMBOLS,
    FEATURE_NAMES,
    TIMEFRAME,
    feature_series,
    load_closed_series,
    paired_points,
)
from tradingbot.research.spearman import SpearmanResult, spearman_corr

MONTH_MIN_N = 30
Pair = tuple[int, float, float]


def nonoverlapping_phase(points: Sequence[Pair], horizon: int, phase: int) -> tuple[Pair, ...]:
    """Keep every ``horizon``-th paired observation starting at ``phase``.

    Windows still share market factors; this is not statistical independence.
    """
    if isinstance(horizon, bool) or not isinstance(horizon, int) or horizon < 1:
        raise ValueError("horizon must be an integer >= 1")
    if isinstance(phase, bool) or not isinstance(phase, int) or phase < 0 or phase >= horizon:
        raise ValueError("phase must be an integer in 0 .. horizon-1")
    ordered = tuple(sorted(points, key=lambda item: item[0]))
    return tuple(ordered[index] for index in range(phase, len(ordered), horizon))


def union_phases(points: Sequence[Pair], horizon: int) -> tuple[Pair, ...]:
    """Concatenate phases without duplicating timestamps."""
    seen: set[int] = set()
    out: list[Pair] = []
    for phase in range(horizon):
        for item in nonoverlapping_phase(points, horizon, phase):
            if item[0] in seen:
                continue
            seen.add(item[0])
            out.append(item)
    return tuple(out)


def month_key(timestamp_ms: int) -> str:
    return datetime.fromtimestamp(timestamp_ms / 1000.0, tz=UTC).strftime("%Y-%m")


def group_by_month(points: Sequence[Pair]) -> dict[str, tuple[Pair, ...]]:
    grouped: dict[str, list[Pair]] = defaultdict(list)
    for item in sorted(points, key=lambda row: row[0]):
        grouped[month_key(item[0])].append(item)
    return {key: tuple(rows) for key, rows in grouped.items()}


def monthly_spearman(
    points: Sequence[Pair],
    *,
    min_n: int = MONTH_MIN_N,
) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for key, rows in group_by_month(points).items():
        xs = [item[1] for item in rows]
        ys = [item[2] for item in rows]
        if len(rows) < min_n:
            out[key] = {
                "n": len(rows),
                "rho": None,
                "reason": f"below_min_n_{min_n}",
            }
            continue
        out[key] = _result_dict(spearman_corr(xs, ys))
    return out


def monthly_summary(by_month: dict[str, dict]) -> dict:
    defined = [row["rho"] for row in by_month.values() if row["rho"] is not None]
    undefined = [key for key, row in by_month.items() if row["rho"] is None]
    return {
        "month_count": len(by_month),
        "n_positive": sum(1 for rho in defined if rho > 0.0),
        "n_negative": sum(1 for rho in defined if rho < 0.0),
        "n_zero": sum(1 for rho in defined if rho == 0.0),
        "n_undefined": len(undefined),
        "undefined_months": undefined,
        "rho_min": min(defined) if defined else None,
        "rho_p50": _median(defined) if defined else None,
        "rho_max": max(defined) if defined else None,
        "rho_pstdev": (pstdev(defined) if len(defined) >= 2 else None),
    }


def cross_section_raw_and_demeaned(
    by_ts: dict[int, list[tuple[str, float, float]]],
    *,
    min_symbols: int = CROSS_SECTION_MIN_SYMBOLS,
) -> dict:
    """Per-timestamp Spearman on raw y and on y minus that timestamp's mean y.

    Subtracting a constant does not change ranks, so per-timestamp Spearman on
    demeaned y matches raw y. Stacked demeaned pairs are a different sample:
    eligible timestamps (n>=min_symbols) concatenated after removing the
    cross-sectional mean return. That is not a full market-factor model.
    """
    raw_rhos: list[float] = []
    demeaned_ts_rhos: list[float] = []
    ns: list[int] = []
    skipped_short = 0
    undefined_raw = 0
    stacked_x: list[float] = []
    stacked_y: list[float] = []
    stacked_y_d: list[float] = []
    for _ts, rows in sorted(by_ts.items()):
        if len(rows) < min_symbols:
            skipped_short += 1
            continue
        xs = [item[1] for item in rows]
        ys = [item[2] for item in rows]
        mean_y = sum(ys) / float(len(ys))
        ys_d = [value - mean_y for value in ys]
        ns.append(len(rows))
        stacked_x.extend(xs)
        stacked_y.extend(ys)
        stacked_y_d.extend(ys_d)
        raw = spearman_corr(xs, ys)
        demeaned = spearman_corr(xs, ys_d)
        if raw.rho is None:
            undefined_raw += 1
        else:
            raw_rhos.append(raw.rho)
        if demeaned.rho is not None:
            demeaned_ts_rhos.append(demeaned.rho)
    return {
        "min_symbols": min_symbols,
        "timestamps_skipped_short": skipped_short,
        "timestamps_undefined_raw": undefined_raw,
        "median_n_symbols": _median(ns) if ns else None,
        "n_symbols_min": min(ns) if ns else None,
        "n_symbols_max": max(ns) if ns else None,
        "raw_per_timestamp": _rho_dist(raw_rhos),
        "demeaned_per_timestamp": _rho_dist(demeaned_ts_rhos),
        "per_timestamp_rank_invariance": True,
        "stacked_eligible_raw": _result_dict(spearman_corr(stacked_x, stacked_y)),
        "stacked_eligible_demeaned": _result_dict(spearman_corr(stacked_x, stacked_y_d)),
        "stacked_eligible_n": len(stacked_x),
        "stacked_sample": (
            f"Timestamps with at least {min_symbols} symbols that have both the "
            "feature and the horizon return; symbols are those present at that "
            "timestamp only. Demeaned y is y minus the mean y of that same set."
        ),
    }


def run_robustness_study(
    *,
    root: Path,
    now_ms: int,
    universe_path: Path | None = None,
    horizons: Sequence[int] = HORIZONS,
    cross_section_min_symbols: int = CROSS_SECTION_MIN_SYMBOLS,
    month_min_n: int = MONTH_MIN_N,
    g_n_path: Path | None = None,
) -> dict:
    universe_path = universe_path or (root / "data" / "processed" / "universe.json")
    symbols = load_universe_symbols(universe_path)
    processed_root = root / "data" / "processed" / "ohlcv"
    pooled_points: dict[str, dict[int, list[Pair]]] = {
        name: {h: [] for h in horizons} for name in FEATURE_NAMES
    }
    pooled_phase: dict[str, dict[int, dict[int, list[Pair]]]] = {
        name: {h: {phase: [] for phase in range(h)} for h in horizons} for name in FEATURE_NAMES
    }
    cs_rows: dict[str, dict[int, dict[int, list[tuple[str, float, float]]]]] = {
        name: {h: defaultdict(list) for h in horizons} for name in FEATURE_NAMES
    }
    per_symbol_phase: dict[str, dict[int, dict[int, list[float]]]] = {
        name: {h: {phase: [] for phase in range(h)} for h in horizons} for name in FEATURE_NAMES
    }
    missing: list[str] = []
    errors: list[dict] = []
    analyzed = 0

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
        analyzed += 1
        features = feature_series(series)
        fwds = forward_returns(series, horizons)
        for name in FEATURE_NAMES:
            for horizon in horizons:
                points = paired_points(features[name], fwds[horizon])
                pooled_points[name][horizon].extend(points)
                for ts, value, fwd in points:
                    cs_rows[name][horizon][ts].append((symbol, value, fwd))
                for phase in range(horizon):
                    subset = nonoverlapping_phase(points, horizon, phase)
                    pooled_phase[name][horizon][phase].extend(subset)
                    result = spearman_corr([item[1] for item in subset], [item[2] for item in subset])
                    if result.rho is not None:
                        per_symbol_phase[name][horizon][phase].append(result.rho)

    nonoverlap: dict = {}
    monthly: dict = {}
    for name in FEATURE_NAMES:
        nonoverlap[name] = {}
        monthly[name] = {}
        for horizon in horizons:
            points = tuple(pooled_points[name][horizon])
            phases = {}
            for phase in range(horizon):
                subset = pooled_phase[name][horizon][phase]
                phases[str(phase)] = _result_dict(
                    spearman_corr([item[1] for item in subset], [item[2] for item in subset])
                )
            union_n = sum(len(pooled_phase[name][horizon][phase]) for phase in range(horizon))
            nonoverlap[name][str(horizon)] = {
                "phases": phases,
                "union_unique": _result_dict(
                    spearman_corr([item[1] for item in points], [item[2] for item in points])
                ),
                "union_n_matches_sum_phases": union_n == len(points),
                "per_symbol_phase_rho": {
                    str(phase): _rho_dist(per_symbol_phase[name][horizon][phase])
                    for phase in range(horizon)
                },
            }
            by_month = monthly_spearman(points, min_n=month_min_n)
            full = spearman_corr([item[1] for item in points], [item[2] for item in points])
            summary = monthly_summary(by_month)
            summary["full_period"] = _result_dict(full)
            summary["full_minus_monthly_p50"] = (
                None
                if full.rho is None or summary["rho_p50"] is None
                else full.rho - summary["rho_p50"]
            )
            monthly[name][str(horizon)] = {"by_month": by_month, "summary": summary}

    cross_section = {
        name: {
            str(h): cross_section_raw_and_demeaned(
                cs_rows[name][h],
                min_symbols=cross_section_min_symbols,
            )
            for h in horizons
        }
        for name in FEATURE_NAMES
    }
    g_n_comparison = _compare_g_n(g_n_path or (root / "data" / "experimental" / "g_n_indicator_forward.json"), nonoverlap)

    return {
        "timeframe": TIMEFRAME,
        "now_ms": now_ms,
        "universe_path": universe_path.as_posix(),
        "universe_symbol_count": len(symbols),
        "analyzed_symbols": analyzed,
        "missing_files": missing,
        "load_errors": errors,
        "horizons": list(horizons),
        "features": list(FEATURE_NAMES),
        "month_min_n": month_min_n,
        "cross_section_min_symbols": cross_section_min_symbols,
        "nonoverlap_independence_disclaimer": (
            "Selecting every h-th valid pair avoids overlapping forward windows "
            "in calendar time when bars are consecutive. It does not make "
            "observations independent: symbols still share market moves, and "
            "features remain serially correlated."
        ),
        "survivorship_listing_lag": True,
        "quote_volume_used": False,
        "nonoverlap": nonoverlap,
        "monthly": monthly,
        "cross_section": cross_section,
        "g_n_comparison": g_n_comparison,
    }


def _compare_g_n(path: Path, nonoverlap: dict) -> dict:
    if not path.exists():
        return {"g_n_path": path.as_posix(), "available": False}
    payload = json.loads(path.read_text(encoding="utf-8"))
    pooled = payload.get("pooled") or {}
    rows = []
    for name in FEATURE_NAMES:
        for horizon in HORIZONS:
            g_n = (pooled.get(name) or {}).get(str(horizon)) or {}
            union = ((nonoverlap.get(name) or {}).get(str(horizon)) or {}).get("union_unique") or {}
            g_n_rho = g_n.get("rho")
            union_rho = union.get("rho")
            rows.append(
                {
                    "feature": name,
                    "horizon": horizon,
                    "g_n_n": g_n.get("n"),
                    "g_n_rho": g_n_rho,
                    "union_n": union.get("n"),
                    "union_rho": union_rho,
                    "same_sign": (
                        None
                        if g_n_rho is None or union_rho is None
                        else (g_n_rho > 0) == (union_rho > 0)
                    ),
                    "rho_diff": (
                        None
                        if g_n_rho is None or union_rho is None
                        else union_rho - g_n_rho
                    ),
                }
            )
    return {"g_n_path": path.as_posix(), "available": True, "rows": rows}


def _result_dict(result: SpearmanResult) -> dict:
    return {"n": result.n, "rho": result.rho, "reason": result.reason}


def _median(values: list[float]) -> float:
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return float(ordered[mid])
    return (ordered[mid - 1] + ordered[mid]) / 2.0


def _rho_dist(values: list[float]) -> dict:
    if not values:
        return {"count": 0, "min": None, "p50": None, "max": None}
    return {
        "count": len(values),
        "min": min(values),
        "p50": _median(values),
        "max": max(values),
    }

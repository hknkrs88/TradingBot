"""Named batch of existing research indicators. No scoring or signals.

Single-series helpers already cover each formula. This dispatcher only calls
those functions with documented defaults. Relative return is omitted because it
needs two ``CandleSeries`` inputs.

Contract
--------
- Allowed names are ``INDICATOR_NAMES`` (sorted unique catalog).
- Unknown or empty names raise ``ValueError`` before any compute.
- Empty ``names`` is valid: no computes, empty ``items`` and ``errors``.
- Duplicate names are collapsed; each selected name is computed at most once.
- ``items`` and ``errors`` are sorted by name, not caller order.
- Defaults: SMA/EMA period 20, ROC / volume ROC 6; other names use the
  underlying function defaults (RSI 14, ADX/ATR 14, BB 20, volume SMA 20,
  MACD 12/26/9, Stoch RSI 14/3/3). There is no per-call period override here.
- Warm-up, gaps, and undefined values stay as skip reasons on points, not
  bundle errors. ``except Exception`` isolates programming/runtime faults per
  name so one failure does not drop others. ``BaseException`` is not caught.
- Each error string is ``{exc_type.__name__}: {exc}`` so the type is prefix-
  readable without a second field.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from tradingbot.indicators.momentum import RSI_PERIOD, roc, rsi, stoch_rsi
from tradingbot.indicators.strength import ADX_PERIOD, adx
from tradingbot.indicators.trend import ema, macd, sma
from tradingbot.indicators.volatility import ATR_PERIOD, atr, bollinger_bands
from tradingbot.indicators.volume import VOLUME_SMA_PERIOD, obv, volume_roc, volume_sma
from tradingbot.strategy.models import CandleSeries

RESEARCH_SMA_PERIOD = 20
RESEARCH_EMA_PERIOD = 20
RESEARCH_ROC_PERIOD = 6
RESEARCH_VOLUME_ROC_PERIOD = 6

INDICATOR_NAMES: tuple[str, ...] = (
    "adx",
    "atr",
    "bollinger_bands",
    "ema",
    "macd",
    "obv",
    "roc",
    "rsi",
    "sma",
    "stoch_rsi",
    "volume_roc",
    "volume_sma",
)


def _compute_sma(series: CandleSeries) -> Any:
    return sma(series, RESEARCH_SMA_PERIOD)


def _compute_ema(series: CandleSeries) -> Any:
    return ema(series, RESEARCH_EMA_PERIOD)


def _compute_roc(series: CandleSeries) -> Any:
    return roc(series, RESEARCH_ROC_PERIOD)


def _compute_volume_roc(series: CandleSeries) -> Any:
    return volume_roc(series, RESEARCH_VOLUME_ROC_PERIOD)


_COMPUTE: dict[str, Callable[[CandleSeries], Any]] = {
    "adx": lambda series: adx(series, ADX_PERIOD),
    "atr": lambda series: atr(series, ATR_PERIOD),
    "bollinger_bands": bollinger_bands,
    "ema": _compute_ema,
    "macd": macd,
    "obv": obv,
    "roc": _compute_roc,
    "rsi": lambda series: rsi(series, RSI_PERIOD),
    "sma": _compute_sma,
    "stoch_rsi": stoch_rsi,
    "volume_roc": _compute_volume_roc,
    "volume_sma": lambda series: volume_sma(series, VOLUME_SMA_PERIOD),
}


@dataclass(frozen=True)
class IndicatorBundle:
    """Successful results and per-name errors. Keys are sorted unique names."""

    symbol: str
    timeframe: str
    items: tuple[tuple[str, Any], ...]
    errors: tuple[tuple[str, str], ...]


def compute_indicators(series: CandleSeries, names: Sequence[str]) -> IndicatorBundle:
    """Compute the selected named indicators. Unknown names raise before any work.

    Failures are recorded per name so other selections still return. Does not
    emit scores or trade signals.
    """
    if any(not isinstance(name, str) or not name for name in names):
        raise ValueError("indicator names must be non-empty strings")
    unknown = sorted({name for name in names if name not in _COMPUTE})
    if unknown:
        raise ValueError(f"unknown indicator names: {unknown}")
    selected = tuple(sorted(set(names)))
    items: list[tuple[str, Any]] = []
    errors: list[tuple[str, str]] = []
    for name in selected:
        try:
            items.append((name, _COMPUTE[name](series)))
        except Exception as exc:
            # Per-name isolation for research batches. Skip reasons are not errors.
            errors.append((name, f"{type(exc).__name__}: {exc}"))
    return IndicatorBundle(
        symbol=series.symbol,
        timeframe=series.timeframe,
        items=tuple(items),
        errors=tuple(errors),
    )

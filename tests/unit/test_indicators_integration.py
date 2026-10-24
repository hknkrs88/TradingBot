"""G-L: stitch research helpers on synthetic series. No scores or signals."""

from __future__ import annotations

import inspect

import pytest

from tradingbot.data.ohlcv.models import Candle
from tradingbot.indicators import (
    RESEARCH_SMA_PERIOD,
    SKIP_GAP,
    SKIP_WARMUP,
    __all__ as INDICATOR_EXPORTS,
    align_indicator_series,
    compute_indicators,
    relative_return,
    sma,
    summarize_indicator_series,
    timestamp_coverage,
    volume_sma,
)
from tradingbot.indicators.research import INDICATOR_NAMES, _COMPUTE
from tradingbot.strategy.models import CandleSeries

TF = 14_400_000
PRICE_NAMES = ("sma", "ema", "roc", "rsi")
VOLUME_NAMES = ("volume_sma", "obv", "volume_roc")


def _candle(index: int, *, close: float, volume: float) -> Candle:
    return Candle(
        timestamp=index * TF,
        open=close,
        high=close + 0.5,
        low=close - 0.5,
        close=close,
        volume=volume,
    )


def _series(
    rows: list[tuple[int, float, float]],
    *,
    symbol: str = "AAA/USDT",
) -> CandleSeries:
    candles = tuple(_candle(index, close=close, volume=volume) for index, close, volume in rows)
    return CandleSeries.from_closed(
        symbol=symbol,
        timeframe="4h",
        candles=candles,
        now_ms=candles[-1].timestamp + TF,
    )


def _linear(n: int = 36, *, gap_at: int | None = None) -> CandleSeries:
    rows: list[tuple[int, float, float]] = []
    for i in range(n):
        if gap_at is not None and i == gap_at:
            continue
        rows.append((i, 10.0 + i * 0.25, float(i + 1)))
    return _series(rows)


def test_all_public_exports_are_unique_and_importable() -> None:
    import tradingbot.indicators as indicators

    assert len(INDICATOR_EXPORTS) == len(set(INDICATOR_EXPORTS))
    for name in INDICATOR_EXPORTS:
        assert hasattr(indicators, name), name
        getattr(indicators, name)


def test_dispatcher_only_calls_existing_functions() -> None:
    import tradingbot.indicators.research as research

    research_src = inspect.getsource(research)
    assert "def sma(" not in research_src
    assert "def rsi(" not in research_src
    assert "return_A" not in research_src
    assert set(_COMPUTE) == set(INDICATOR_NAMES)


def test_price_volume_align_quality_and_dispatcher_match() -> None:
    series = _linear()
    bundle = compute_indicators(series, PRICE_NAMES + VOLUME_NAMES)
    by_name = dict(bundle.items)
    assert bundle.errors == ()
    assert [name for name, _ in bundle.items] == sorted(PRICE_NAMES + VOLUME_NAMES)

    direct_sma = sma(series, RESEARCH_SMA_PERIOD)
    assert by_name["sma"] == direct_sma

    vsma = by_name["volume_sma"]
    assert vsma == volume_sma(series)
    quote_mean = sum(c.close * c.volume for c in series.candles[-RESEARCH_SMA_PERIOD :]) / float(
        RESEARCH_SMA_PERIOD
    )
    last_vsma = next(point.value for point in reversed(vsma.points) if point.value is not None)
    assert last_vsma != pytest.approx(quote_mean)
    last_base = sum(c.volume for c in series.candles[-RESEARCH_SMA_PERIOD :]) / float(RESEARCH_SMA_PERIOD)
    assert last_vsma == pytest.approx(last_base)

    aligned = align_indicator_series(by_name["sma"], by_name["ema"])
    assert aligned.timestamps == tuple(c.timestamp for c in series.candles)
    assert aligned.left_only == ()
    assert aligned.right_only == ()
    coverage = timestamp_coverage(by_name["sma"], by_name["ema"])
    assert coverage.coverage_ratio == pytest.approx(1.0)
    assert coverage.common_count == len(series.candles)

    quality = summarize_indicator_series(direct_sma)
    assert dict(quality.skip_counts)[SKIP_WARMUP] == RESEARCH_SMA_PERIOD - 1
    assert quality.valued_count == len(series.candles) - (RESEARCH_SMA_PERIOD - 1)
    assert quality.first_valued_timestamp == series.candles[RESEARCH_SMA_PERIOD - 1].timestamp
    assert quality.first_timestamp == series.candles[0].timestamp


def test_gap_skips_are_consistent_across_helpers() -> None:
    series = _linear(n=30, gap_at=12)
    bundle = compute_indicators(series, ("sma", "roc"))
    by_name = dict(bundle.items)
    roc_series = by_name["roc"]
    gap_ts = 13 * TF
    roc_at_gap = next(point for point in roc_series.points if point.timestamp == gap_ts)
    assert roc_at_gap.skip_reason == SKIP_GAP
    assert roc_at_gap.value is None

    aligned = align_indicator_series(by_name["sma"], roc_series)
    assert gap_ts in aligned.timestamps
    quality = summarize_indicator_series(roc_series)
    assert dict(quality.skip_counts)[SKIP_GAP] >= 1
    assert quality.point_count == len(series.candles)
    assert quality.valued_count + sum(dict(quality.skip_counts).values()) == quality.point_count


def test_relative_return_is_not_timestamp_coverage() -> None:
    left = _linear(n=10)
    right = _series([(i, 20.0 + i, 1.0) for i in range(10)], symbol="BBB/USDT")
    rel = relative_return(left, right)
    coverage = timestamp_coverage(
        compute_indicators(left, ("sma",)).items[0][1],
        compute_indicators(right, ("sma",)).items[0][1],
    )
    last_rel = next(point.value for point in reversed(rel.relative.points) if point.value is not None)
    assert last_rel != pytest.approx(coverage.coverage_ratio)
    assert rel.symbol_a == "AAA/USDT"
    assert rel.symbol_b == "BBB/USDT"
    assert coverage.left_symbol == "AAA/USDT"
    assert coverage.right_symbol == "BBB/USDT"


def test_empty_and_skip_heavy_series_do_not_error() -> None:
    empty = CandleSeries.from_closed(symbol="AAA/USDT", timeframe="4h", candles=(), now_ms=0)
    bundle = compute_indicators(empty, ("sma", "obv", "rsi"))
    assert bundle.errors == ()
    for _, result in bundle.items:
        report = summarize_indicator_series(result)
        assert report.point_count == 0
        assert report.valued_ratio == 0.0
    short = _linear(n=3)
    skip_bundle = compute_indicators(short, ("sma", "rsi", "macd"))
    assert skip_bundle.errors == ()
    sma_short = dict(skip_bundle.items)["sma"]
    report = summarize_indicator_series(sma_short)
    assert report.valued_count == 0
    assert dict(report.skip_counts)[SKIP_WARMUP] == 3
    aligned = align_indicator_series(sma_short, dict(skip_bundle.items)["rsi"])
    assert len(aligned.timestamps) == 3
    coverage = timestamp_coverage(sma_short, sma_short)
    assert coverage.coverage_ratio == pytest.approx(1.0)


def test_pipeline_is_deterministic() -> None:
    series = _linear(gap_at=8)
    names = ("sma", "volume_sma", "roc")
    first = compute_indicators(series, names)
    second = compute_indicators(series, names)
    assert first == second
    aligned = align_indicator_series(dict(first.items)["sma"], dict(second.items)["roc"])
    assert aligned == align_indicator_series(dict(second.items)["sma"], dict(first.items)["roc"])
    assert summarize_indicator_series(dict(first.items)["sma"]) == summarize_indicator_series(
        dict(second.items)["sma"]
    )

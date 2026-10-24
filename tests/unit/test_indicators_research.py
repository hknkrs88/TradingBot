import pytest

from tradingbot.data.ohlcv.models import Candle
from tradingbot.indicators import (
    RESEARCH_EMA_PERIOD,
    RESEARCH_ROC_PERIOD,
    RESEARCH_SMA_PERIOD,
    RESEARCH_VOLUME_ROC_PERIOD,
    SKIP_WARMUP,
    adx,
    atr,
    bollinger_bands,
    compute_indicators,
    ema,
    macd,
    obv,
    roc,
    rsi,
    sma,
    stoch_rsi,
    volume_roc,
    volume_sma,
)
from tradingbot.indicators.research import INDICATOR_NAMES, _COMPUTE
from tradingbot.strategy.models import CandleSeries

TF = 14_400_000


def _series(n: int = 40) -> CandleSeries:
    candles = tuple(
        Candle(
            timestamp=i * TF,
            open=10.0 + i * 0.1,
            high=10.5 + i * 0.1,
            low=9.5 + i * 0.1,
            close=10.2 + i * 0.1,
            volume=float(i + 1),
        )
        for i in range(n)
    )
    return CandleSeries.from_closed(
        symbol="AAA/USDT",
        timeframe="4h",
        candles=candles,
        now_ms=candles[-1].timestamp + TF,
    )


def test_bundle_matches_direct_calls_for_selected_names() -> None:
    series = _series()
    bundle = compute_indicators(series, ("sma", "ema", "roc", "obv"))
    by_name = dict(bundle.items)
    assert set(by_name) == {"ema", "obv", "roc", "sma"}
    assert by_name["sma"] == sma(series, RESEARCH_SMA_PERIOD)
    assert by_name["ema"] == ema(series, RESEARCH_EMA_PERIOD)
    assert by_name["roc"] == roc(series, RESEARCH_ROC_PERIOD)
    assert by_name["obv"] == obv(series)
    assert bundle.errors == ()
    assert bundle.symbol == "AAA/USDT"
    assert bundle.timeframe == "4h"


def test_unselected_indicators_are_absent() -> None:
    series = _series()
    bundle = compute_indicators(series, ("rsi",))
    assert [name for name, _ in bundle.items] == ["rsi"]
    assert dict(bundle.items)["rsi"] == rsi(series, 14)


def test_result_keys_are_sorted_unique_not_caller_order() -> None:
    bundle = compute_indicators(_series(), ("rsi", "sma", "rsi", "atr"))
    assert [name for name, _ in bundle.items] == ["atr", "rsi", "sma"]


def test_duplicate_names_compute_once(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"sma": 0}
    real_sma = sma

    def counted(series: CandleSeries, period: int) -> object:
        calls["sma"] += 1
        return real_sma(series, period)

    monkeypatch.setattr("tradingbot.indicators.research.sma", counted)
    bundle = compute_indicators(_series(), ("sma", "sma", "sma"))
    assert calls["sma"] == 1
    assert [name for name, _ in bundle.items] == ["sma"]


def test_empty_selection_computes_nothing() -> None:
    series = _series()
    bundle = compute_indicators(series, ())
    assert bundle.items == ()
    assert bundle.errors == ()
    assert bundle.symbol == series.symbol
    assert bundle.timeframe == series.timeframe


def test_unknown_name_raises_before_compute() -> None:
    with pytest.raises(ValueError, match="unknown indicator"):
        compute_indicators(_series(), ("sma", "not_an_indicator"))


def test_empty_and_non_string_names_raise() -> None:
    series = _series()
    with pytest.raises(ValueError, match="non-empty strings"):
        compute_indicators(series, ("",))
    with pytest.raises(ValueError, match="non-empty strings"):
        compute_indicators(series, (None,))  # type: ignore[arg-type]


@pytest.mark.parametrize("name", INDICATOR_NAMES)
def test_each_catalog_name_dispatches(name: str) -> None:
    series = _series()
    bundle = compute_indicators(series, (name,))
    assert bundle.errors == ()
    assert [item_name for item_name, _ in bundle.items] == [name]


def test_all_catalog_names_run_and_preserve_skips() -> None:
    series = _series(8)
    bundle = compute_indicators(series, INDICATOR_NAMES)
    by_name = dict(bundle.items)
    assert bundle.errors == ()
    assert tuple(by_name) == INDICATOR_NAMES
    assert set(_COMPUTE) == set(INDICATOR_NAMES)
    assert by_name["macd"] == macd(series)
    assert by_name["adx"] == adx(series)
    assert by_name["atr"] == atr(series)
    assert by_name["bollinger_bands"] == bollinger_bands(series)
    assert by_name["stoch_rsi"] == stoch_rsi(series)
    assert by_name["volume_sma"] == volume_sma(series)
    assert by_name["volume_roc"] == volume_roc(series, RESEARCH_VOLUME_ROC_PERIOD)
    first = by_name["sma"].points[0]
    assert first.skip_reason == SKIP_WARMUP
    assert first.value is None
    assert first.timestamp == series.candles[0].timestamp


def test_one_failure_does_not_drop_other_results(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(series: CandleSeries, period: int = 20) -> None:
        raise RuntimeError("sma failed")

    monkeypatch.setattr("tradingbot.indicators.research.sma", boom)
    bundle = compute_indicators(_series(), ("sma", "obv"))
    by_name = dict(bundle.items)
    assert "obv" in by_name
    assert "sma" not in by_name
    assert bundle.errors == (("sma", "RuntimeError: sma failed"),)


def test_multiple_errors_are_sorted_by_name(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom_sma(series: CandleSeries, period: int = 20) -> None:
        raise RuntimeError("sma failed")

    def boom_ema(series: CandleSeries, period: int = 20) -> None:
        raise TypeError("ema failed")

    monkeypatch.setattr("tradingbot.indicators.research.sma", boom_sma)
    monkeypatch.setattr("tradingbot.indicators.research.ema", boom_ema)
    bundle = compute_indicators(_series(), ("sma", "obv", "ema"))
    assert [name for name, _ in bundle.items] == ["obv"]
    assert bundle.errors == (
        ("ema", "TypeError: ema failed"),
        ("sma", "RuntimeError: sma failed"),
    )


def test_dispatcher_has_no_period_override() -> None:
    with pytest.raises(TypeError):
        compute_indicators(_series(), ("sma",), period=0)  # type: ignore[call-arg]


def test_bundle_is_deterministic() -> None:
    series = _series()
    assert compute_indicators(series, ("macd", "rsi")) == compute_indicators(series, ("rsi", "macd"))

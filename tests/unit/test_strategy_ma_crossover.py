from pathlib import Path

import pytest

from tradingbot.data.ohlcv.models import Candle
from tradingbot.strategy.ma_crossover import MACrossover, MACrossoverParams
from tradingbot.strategy.models import CandleSeries, Signal

HOUR = 3_600_000
FOUR_H = 14_400_000
SRC = Path(__file__).resolve().parents[2] / "src" / "tradingbot" / "strategy"


def _c(ts: int, close: float) -> Candle:
    return Candle(timestamp=ts, open=close, high=close, low=close, close=close, volume=1)


def _series(
    closes: list[float],
    *,
    timeframe: str = "1h",
    symbol: str = "BTC/USDT",
) -> CandleSeries:
    step = HOUR if timeframe == "1h" else FOUR_H
    candles = [_c(i * step, close) for i, close in enumerate(closes)]
    last_ts = candles[-1].timestamp if candles else 0
    tf_ms = HOUR if timeframe == "1h" else FOUR_H
    return CandleSeries.from_closed(
        symbol=symbol,
        timeframe=timeframe,
        candles=candles,
        now_ms=last_ts + 2 * tf_ms,
    )


def _strategy(fast: int = 2, slow: int = 3) -> MACrossover:
    return MACrossover(MACrossoverParams(fast=fast, slow=slow))


def test_params_must_be_positive() -> None:
    with pytest.raises(ValueError, match="positive"):
        MACrossoverParams(fast=0, slow=3)
    with pytest.raises(ValueError, match="positive"):
        MACrossoverParams(fast=2, slow=-1)


def test_params_fast_must_be_less_than_slow() -> None:
    with pytest.raises(ValueError, match="fast"):
        MACrossoverParams(fast=3, slow=3)
    with pytest.raises(ValueError, match="fast"):
        MACrossoverParams(fast=5, slow=2)


def test_candleseries_rejects_unsorted() -> None:
    candles = (_c(HOUR, 1), _c(0, 1))
    with pytest.raises(ValueError, match="sorted"):
        CandleSeries.from_closed(
            symbol="BTC/USDT",
            timeframe="1h",
            candles=candles,
            now_ms=4 * HOUR,
        )


def test_candleseries_rejects_duplicate_timestamp() -> None:
    candles = (_c(0, 1), _c(0, 2))
    with pytest.raises(ValueError, match="duplicate"):
        CandleSeries.from_closed(
            symbol="BTC/USDT",
            timeframe="1h",
            candles=candles,
            now_ms=4 * HOUR,
        )


def test_candleseries_rejects_invalid_timeframe() -> None:
    with pytest.raises(ValueError):
        CandleSeries.from_closed(
            symbol="BTC/USDT",
            timeframe="15m",
            candles=(_c(0, 1),),
            now_ms=4 * HOUR,
        )


def test_candleseries_rejects_incomplete_candle() -> None:
    with pytest.raises(ValueError, match="incomplete"):
        CandleSeries.from_closed(
            symbol="BTC/USDT",
            timeframe="1h",
            candles=(_c(0, 1), _c(HOUR, 1)),
            now_ms=HOUR,
        )


def test_warmup_is_hold() -> None:
    signals = _strategy().generate(_series([1.0, 2.0, 3.0, 4.0]))
    assert signals[0].side == "hold"
    assert signals[0].reason == "warmup"
    assert signals[1].side == "hold"
    assert signals[1].reason == "warmup"


def test_fast_above_slow_is_buy() -> None:
    signals = _strategy().generate(_series([1.0, 1.0, 1.0, 10.0, 10.0]))
    assert signals[3].side == "buy"
    assert signals[4].side == "buy"
    assert signals[4].reason == "fast_above_slow"


def test_fast_below_slow_is_sell() -> None:
    signals = _strategy().generate(_series([10.0, 10.0, 10.0, 1.0, 1.0]))
    assert signals[3].side == "sell"
    assert signals[4].side == "sell"
    assert signals[4].reason == "fast_below_slow"


def test_equal_mas_are_hold() -> None:
    signals = _strategy().generate(_series([2.0, 2.0, 2.0, 2.0]))
    assert signals[2].side == "hold"
    assert signals[2].reason == "equal"
    assert signals[3].side == "hold"
    assert signals[3].reason == "equal"


def test_cross_up_reason() -> None:
    signals = _strategy().generate(_series([1.0, 1.0, 1.0, 10.0]))
    assert signals[2].side == "hold"
    assert signals[3].side == "buy"
    assert signals[3].reason == "cross_up"


def test_cross_down_reason() -> None:
    signals = _strategy().generate(_series([10.0, 10.0, 10.0, 1.0]))
    assert signals[3].side == "sell"
    assert signals[3].reason == "cross_down"


def test_signal_timestamp_matches_candle() -> None:
    series = _series([1.0, 1.0, 1.0, 10.0])
    signals = _strategy().generate(series)
    assert [item.timestamp for item in signals] == [c.timestamp for c in series.candles]


def test_output_length_matches_input() -> None:
    series = _series([1.0, 2.0, 3.0, 4.0, 5.0])
    signals = _strategy().generate(series)
    assert len(signals) == len(series.candles)


def test_prefix_matches_full_series() -> None:
    full = _series([1.0, 1.0, 1.0, 10.0, 10.0, 1.0, 1.0])
    strategy = _strategy()
    full_signals = strategy.generate(full)
    for index in range(len(full.candles)):
        prefix = CandleSeries.from_closed(
            symbol=full.symbol,
            timeframe=full.timeframe,
            candles=full.candles[: index + 1],
            now_ms=full.candles[-1].timestamp + 2 * HOUR,
        )
        assert strategy.generate(prefix)[-1] == full_signals[index]


def test_append_does_not_change_prior_signals() -> None:
    base = _series([1.0, 1.0, 1.0, 10.0, 10.0])
    extra = list(base.candles) + [_c(5 * HOUR, 4.0)]
    extended = CandleSeries.from_closed(
        symbol=base.symbol,
        timeframe=base.timeframe,
        candles=extra,
        now_ms=8 * HOUR,
    )
    strategy = _strategy()
    first = strategy.generate(base)
    second = strategy.generate(extended)
    assert second[: len(first)] == first


def test_strategy_has_no_mutable_state() -> None:
    series = _series([1.0, 1.0, 1.0, 10.0])
    strategy = _strategy()
    first = strategy.generate(series)
    second = strategy.generate(series)
    assert first == second
    other = _strategy()
    assert other.generate(series) == first


def test_generate_does_not_accept_now_ms() -> None:
    import inspect

    params = inspect.signature(MACrossover.generate).parameters
    assert "now_ms" not in params
    assert list(params) == ["self", "series"]


def test_strategy_has_no_broker_or_data_layer_imports() -> None:
    forbidden = (
        "ccxt",
        "tradingbot.broker",
        "tradingbot.execution",
        "tradingbot.risk",
        "tradingbot.portfolio",
        "load_processed",
        "sync_ohlcv",
        "write_ohlcv_manifest",
        "create_binance",
        "create_public_binance",
    )
    for path in SRC.glob("*.py"):
        text = path.read_text(encoding="utf-8")
        for token in forbidden:
            assert token not in text, f"{path.name} contains {token}"


def test_signal_has_no_fill_or_risk_fields() -> None:
    signal = Signal(
        symbol="BTC/USDT",
        timeframe="1h",
        timestamp=0,
        side="buy",
        strategy="ma_crossover",
        reason="cross_up",
    )
    assert not hasattr(signal, "quantity")
    assert not hasattr(signal, "price")
    assert not hasattr(signal, "stop")
    assert not hasattr(signal, "take_profit")
    assert not hasattr(signal, "order_type")
    assert not hasattr(signal, "portfolio")
    assert not hasattr(signal, "position")


def test_same_logic_on_4h_series() -> None:
    one_h = _strategy().generate(_series([1.0, 1.0, 1.0, 10.0], timeframe="1h"))
    four_h = _strategy().generate(_series([1.0, 1.0, 1.0, 10.0], timeframe="4h"))
    assert [item.side for item in one_h] == [item.side for item in four_h]
    assert [item.reason for item in one_h] == [item.reason for item in four_h]
    assert four_h[-1].timeframe == "4h"
    assert four_h[-1].timestamp == 3 * FOUR_H

from pathlib import Path

import pytest

from tradingbot.backtest.engine import run_backtest
from tradingbot.backtest.models import BacktestConfig
from tradingbot.data.ohlcv.models import Candle
from tradingbot.strategy.models import CandleSeries, Signal

HOUR = 3_600_000
SRC = Path(__file__).resolve().parents[2] / "src" / "tradingbot" / "backtest"


def _c(ts: int, open_: float, close: float) -> Candle:
    high = max(open_, close)
    low = min(open_, close)
    return Candle(timestamp=ts, open=open_, high=high, low=low, close=close, volume=1)


def _series(bars: list[tuple[float, float]], *, symbol: str = "BTC/USDT") -> CandleSeries:
    candles = [_c(i * HOUR, open_, close) for i, (open_, close) in enumerate(bars)]
    return CandleSeries.from_closed(
        symbol=symbol,
        timeframe="1h",
        candles=candles,
        now_ms=candles[-1].timestamp + 2 * HOUR,
    )


def _signals(series: CandleSeries, sides: list[str]) -> list[Signal]:
    return [
        Signal(
            symbol=series.symbol,
            timeframe=series.timeframe,
            timestamp=series.candles[i].timestamp,
            side=side,
            strategy="test",
            reason="fixture",
        )
        for i, side in enumerate(sides)
    ]


def _cfg(cash: float = 10_000.0, rate: float = 0.0) -> BacktestConfig:
    return BacktestConfig(initial_cash=cash, commission_rate=rate)


def test_buy_fills_next_bar_open() -> None:
    series = _series([(10.0, 11.0), (20.0, 21.0), (22.0, 23.0)])
    result = run_backtest(series, _signals(series, ["buy", "hold", "hold"]), _cfg())
    assert result.final_quantity == pytest.approx(10_000.0 / 20.0)
    assert result.equity[0].quantity == pytest.approx(0.0)
    assert result.equity[1].quantity == pytest.approx(500.0)


def test_buy_does_not_fill_at_signal_bar_close() -> None:
    series = _series([(10.0, 99.0), (20.0, 50.0)])
    result = run_backtest(series, _signals(series, ["buy", "hold"]), _cfg())
    assert result.final_quantity == pytest.approx(10_000.0 / 20.0)
    assert result.final_quantity != pytest.approx(10_000.0 / 99.0)
    assert result.final_quantity != pytest.approx(10_000.0 / 50.0)


def test_sell_fills_next_bar_open() -> None:
    series = _series([(10.0, 10.0), (20.0, 20.0), (30.0, 30.0)])
    result = run_backtest(series, _signals(series, ["buy", "sell", "hold"]), _cfg())
    assert len(result.trades) == 1
    trade = result.trades[0]
    assert trade.entry_timestamp == HOUR
    assert trade.entry_price == pytest.approx(20.0)
    assert trade.exit_timestamp == 2 * HOUR
    assert trade.exit_price == pytest.approx(30.0)
    assert result.final_quantity == pytest.approx(0.0)


def test_repeated_buy_is_single_entry() -> None:
    series = _series([(8.0, 8.0), (10.0, 10.0), (11.0, 11.0), (12.0, 12.0)])
    result = run_backtest(series, _signals(series, ["buy", "buy", "buy", "hold"]), _cfg())
    assert result.final_quantity == pytest.approx(10_000.0 / 10.0)
    assert len(result.trades) == 0
    assert [point.quantity > 0 for point in result.equity] == [False, True, True, True]


def test_flat_sell_is_noop() -> None:
    series = _series([(10.0, 10.0), (20.0, 20.0), (30.0, 30.0)])
    result = run_backtest(series, _signals(series, ["sell", "sell", "hold"]), _cfg())
    assert result.trades == ()
    assert result.final_quantity == pytest.approx(0.0)
    assert result.final_cash == pytest.approx(10_000.0)
    assert result.final_equity == pytest.approx(10_000.0)


def test_long_sell_closes_position() -> None:
    series = _series([(10.0, 10.0), (10.0, 10.0), (12.0, 12.0)])
    result = run_backtest(series, _signals(series, ["buy", "sell", "hold"]), _cfg())
    assert len(result.trades) == 1
    assert result.final_quantity == pytest.approx(0.0)


def test_quantity_never_goes_short() -> None:
    series = _series([(10.0, 10.0), (11.0, 11.0), (9.0, 9.0), (8.0, 8.0)])
    result = run_backtest(series, _signals(series, ["sell", "buy", "sell", "sell"]), _cfg())
    assert all(point.quantity >= 0 for point in result.equity)
    assert result.final_quantity == pytest.approx(0.0)


def test_entry_commission_quantity() -> None:
    series = _series([(10.0, 10.0), (100.0, 100.0), (100.0, 100.0)])
    rate = 0.001
    cash = 10_000.0
    result = run_backtest(series, _signals(series, ["buy", "hold", "hold"]), _cfg(cash, rate))
    expected_qty = cash / (100.0 * (1.0 + rate))
    expected_fee = expected_qty * 100.0 * rate
    assert result.final_quantity == pytest.approx(expected_qty)
    assert result.equity[1].cash == pytest.approx(0.0, abs=1e-9)


def test_exit_commission_and_pnl() -> None:
    series = _series([(10.0, 10.0), (100.0, 100.0), (110.0, 110.0)])
    rate = 0.001
    cash = 10_000.0
    result = run_backtest(series, _signals(series, ["buy", "sell", "hold"]), _cfg(cash, rate))
    qty = cash / (100.0 * (1.0 + rate))
    entry_fee = qty * 100.0 * rate
    exit_fee = qty * 110.0 * rate
    pnl = (qty * 110.0 - exit_fee) - (qty * 100.0 + entry_fee)
    trade = result.trades[0]
    assert trade.quantity == pytest.approx(qty)
    assert trade.entry_fee == pytest.approx(entry_fee)
    assert trade.exit_fee == pytest.approx(exit_fee)
    assert trade.pnl == pytest.approx(pnl)
    entry_cost = qty * 100.0 + entry_fee
    assert trade.return_pct == pytest.approx(pnl / entry_cost * 100.0)


def test_equity_one_point_per_bar() -> None:
    series = _series([(1.0, 1.0), (2.0, 3.0), (4.0, 5.0)])
    result = run_backtest(series, _signals(series, ["hold", "hold", "hold"]), _cfg())
    assert len(result.equity) == 3
    assert [point.timestamp for point in result.equity] == [c.timestamp for c in series.candles]
    assert result.equity[0].equity == pytest.approx(10_000.0)


def test_open_position_marked_to_last_close_not_force_closed() -> None:
    series = _series([(10.0, 10.0), (20.0, 25.0), (26.0, 40.0)])
    result = run_backtest(series, _signals(series, ["buy", "hold", "hold"]), _cfg())
    qty = 10_000.0 / 20.0
    assert result.trades == ()
    assert result.final_quantity == pytest.approx(qty)
    assert result.final_cash == pytest.approx(0.0, abs=1e-9)
    assert result.final_equity == pytest.approx(qty * 40.0)
    assert result.unfilled_signal is None


def test_last_bar_buy_is_unfilled() -> None:
    series = _series([(10.0, 11.0), (12.0, 13.0)])
    result = run_backtest(series, _signals(series, ["hold", "buy"]), _cfg())
    assert result.unfilled_signal is not None
    assert result.unfilled_signal.side == "buy"
    assert result.final_quantity == pytest.approx(0.0)
    assert result.final_cash == pytest.approx(10_000.0)


def test_last_bar_sell_unfilled_only_when_long() -> None:
    long_series = _series([(10.0, 10.0), (20.0, 20.0), (30.0, 30.0)])
    long_result = run_backtest(
        long_series, _signals(long_series, ["buy", "hold", "sell"]), _cfg()
    )
    assert long_result.unfilled_signal is not None
    assert long_result.unfilled_signal.side == "sell"
    assert long_result.final_quantity > 0
    assert long_result.trades == ()

    flat_series = _series([(10.0, 10.0), (20.0, 20.0)])
    flat_result = run_backtest(
        flat_series, _signals(flat_series, ["hold", "sell"]), _cfg()
    )
    assert flat_result.unfilled_signal is None
    assert flat_result.final_quantity == pytest.approx(0.0)


def test_last_bar_hold_has_no_unfilled() -> None:
    series = _series([(10.0, 10.0), (20.0, 20.0)])
    result = run_backtest(series, _signals(series, ["buy", "hold"]), _cfg())
    assert result.unfilled_signal is None
    assert result.final_quantity > 0


def test_last_bar_buy_while_long_is_not_unfilled() -> None:
    series = _series([(10.0, 10.0), (20.0, 20.0), (21.0, 21.0)])
    result = run_backtest(series, _signals(series, ["buy", "buy", "buy"]), _cfg())
    assert result.unfilled_signal is None
    assert result.final_quantity > 0


def test_signal_timestamp_mismatch_fails() -> None:
    series = _series([(10.0, 10.0), (11.0, 11.0)])
    signals = _signals(series, ["hold", "hold"])
    signals[1] = Signal(
        symbol=series.symbol,
        timeframe=series.timeframe,
        timestamp=99,
        side="hold",
        strategy="test",
        reason="bad",
    )
    with pytest.raises(ValueError, match="timestamp"):
        run_backtest(series, signals, _cfg())


def test_signal_length_mismatch_fails() -> None:
    series = _series([(10.0, 10.0), (11.0, 11.0)])
    with pytest.raises(ValueError, match="length"):
        run_backtest(series, _signals(series, ["hold"]), _cfg())


def test_wrong_symbol_and_timeframe_fail() -> None:
    series = _series([(10.0, 10.0)])
    bad_symbol = [
        Signal(
            symbol="ETH/USDT",
            timeframe="1h",
            timestamp=0,
            side="hold",
            strategy="test",
            reason="x",
        )
    ]
    with pytest.raises(ValueError, match="symbol"):
        run_backtest(series, bad_symbol, _cfg())
    bad_tf = [
        Signal(
            symbol="BTC/USDT",
            timeframe="4h",
            timestamp=0,
            side="hold",
            strategy="test",
            reason="x",
        )
    ]
    with pytest.raises(ValueError, match="timeframe"):
        run_backtest(series, bad_tf, _cfg())


def test_invalid_side_fails() -> None:
    series = _series([(10.0, 10.0)])
    signals = [
        Signal(
            symbol="BTC/USDT",
            timeframe="1h",
            timestamp=0,
            side="short",
            strategy="test",
            reason="x",
        )
    ]
    with pytest.raises(ValueError, match="side"):
        run_backtest(series, signals, _cfg())


def test_empty_series_fails() -> None:
    series = CandleSeries(symbol="BTC/USDT", timeframe="1h", candles=())
    with pytest.raises(ValueError, match="empty"):
        run_backtest(series, [], _cfg())


def test_invalid_config() -> None:
    with pytest.raises(ValueError, match="initial_cash"):
        BacktestConfig(initial_cash=0, commission_rate=0.0)
    with pytest.raises(ValueError, match="commission_rate"):
        BacktestConfig(initial_cash=100, commission_rate=-0.01)
    with pytest.raises(ValueError, match="commission_rate"):
        BacktestConfig(initial_cash=100, commission_rate=1.0)


def test_result_is_deterministic() -> None:
    series = _series([(10.0, 12.0), (20.0, 18.0), (25.0, 30.0), (22.0, 22.0)])
    signals = _signals(series, ["buy", "hold", "sell", "hold"])
    first = run_backtest(series, signals, _cfg(5_000.0, 0.001))
    second = run_backtest(series, signals, _cfg(5_000.0, 0.001))
    assert first == second


def test_engine_has_no_broker_or_ccxt_imports() -> None:
    forbidden = ("ccxt", "tradingbot.broker", "create_binance", "place_order")
    for path in SRC.glob("*.py"):
        text = path.read_text(encoding="utf-8")
        for token in forbidden:
            assert token not in text, f"{path.name} contains {token}"
    engine = (SRC / "engine.py").read_text(encoding="utf-8")
    assert "ma_crossover" not in engine
    assert "generate(" not in engine

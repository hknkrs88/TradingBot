import pytest

from tradingbot.backtest.models import BacktestResult, EquityPoint, Trade
from tradingbot.backtest.performance import analyze_performance
from tradingbot.data.ohlcv.models import Candle
from tradingbot.strategy.models import CandleSeries

HOUR = 3_600_000


def _candle(ts: int, close: float) -> Candle:
    return Candle(timestamp=ts, open=close, high=close, low=close, close=close, volume=1)


def _series(closes: list[float]) -> CandleSeries:
    candles = [_candle(i * HOUR, close) for i, close in enumerate(closes)]
    return CandleSeries.from_closed(
        symbol="BTC/USDT",
        timeframe="1h",
        candles=candles,
        now_ms=candles[-1].timestamp + 2 * HOUR,
    )


def _point(ts: int, equity: float, quantity: float = 0.0) -> EquityPoint:
    position_value = quantity * 1.0 if quantity else 0.0
    cash = equity - position_value
    return EquityPoint(
        timestamp=ts,
        cash=cash,
        quantity=quantity,
        position_value=position_value,
        equity=equity,
    )


def _trade(pnl: float) -> Trade:
    return Trade(
        symbol="BTC/USDT",
        entry_timestamp=0,
        exit_timestamp=HOUR,
        entry_price=10.0,
        exit_price=11.0,
        quantity=1.0,
        entry_fee=0.0,
        exit_fee=0.0,
        pnl=pnl,
        return_pct=0.0,
    )


def _result(
    equities: list[float],
    *,
    quantities: list[float] | None = None,
    trades: tuple[Trade, ...] = (),
) -> BacktestResult:
    qty = quantities if quantities is not None else [0.0] * len(equities)
    points = tuple(
        _point(i * HOUR, equity, quantity)
        for i, (equity, quantity) in enumerate(zip(equities, qty, strict=True))
    )
    return BacktestResult(
        trades=trades,
        equity=points,
        final_equity=points[-1].equity,
        final_cash=points[-1].cash,
        final_quantity=points[-1].quantity,
        unfilled_signal=None,
        metadata={"initial_cash": equities[0], "bar_count": len(equities)},
    )


def test_zero_trades_undefined_trade_stats() -> None:
    series = _series([10.0, 11.0, 12.0])
    result = _result([100.0, 100.0, 110.0])
    report = analyze_performance(result, series)
    assert report.trade_count == 0
    assert report.win_rate is None
    assert report.average_trade_pnl is None
    assert report.average_win is None
    assert report.average_loss is None
    assert report.profit_factor is None
    assert report.total_return == pytest.approx(0.10)
    assert report.initial_equity == pytest.approx(100.0)
    assert report.final_equity == pytest.approx(110.0)


def test_all_winning_trades() -> None:
    series = _series([10.0, 10.0, 10.0])
    result = _result([100.0, 110.0, 130.0], trades=(_trade(10.0), _trade(20.0)))
    report = analyze_performance(result, series)
    assert report.win_count == 2
    assert report.loss_count == 0
    assert report.breakeven_count == 0
    assert report.win_rate == pytest.approx(1.0)
    assert report.gross_profit == pytest.approx(30.0)
    assert report.gross_loss == pytest.approx(0.0)
    assert report.profit_factor is None
    assert report.average_win == pytest.approx(15.0)
    assert report.average_loss is None
    assert report.average_trade_pnl == pytest.approx(15.0)


def test_all_losing_trades() -> None:
    series = _series([10.0, 10.0, 10.0])
    result = _result([100.0, 90.0, 70.0], trades=(_trade(-10.0), _trade(-20.0)))
    report = analyze_performance(result, series)
    assert report.win_count == 0
    assert report.loss_count == 2
    assert report.win_rate == pytest.approx(0.0)
    assert report.gross_profit == pytest.approx(0.0)
    assert report.gross_loss == pytest.approx(30.0)
    assert report.profit_factor == pytest.approx(0.0)
    assert report.average_win is None
    assert report.average_loss == pytest.approx(-15.0)
    assert report.average_trade_pnl == pytest.approx(-15.0)


def test_mixed_wins_losses_breakeven() -> None:
    series = _series([10.0, 10.0, 10.0, 10.0])
    result = _result(
        [100.0, 110.0, 100.0, 100.0],
        trades=(_trade(10.0), _trade(-10.0), _trade(0.0)),
    )
    report = analyze_performance(result, series)
    assert report.trade_count == 3
    assert report.win_count == 1
    assert report.loss_count == 1
    assert report.breakeven_count == 1
    assert report.win_rate == pytest.approx(1 / 3)
    assert report.gross_profit == pytest.approx(10.0)
    assert report.gross_loss == pytest.approx(10.0)
    assert report.profit_factor == pytest.approx(1.0)
    assert report.average_win == pytest.approx(10.0)
    assert report.average_loss == pytest.approx(-10.0)
    assert report.average_trade_pnl == pytest.approx(0.0)


def test_gross_loss_zero_profit_factor_none() -> None:
    series = _series([10.0, 10.0])
    result = _result([100.0, 105.0], trades=(_trade(5.0),))
    report = analyze_performance(result, series)
    assert report.gross_loss == pytest.approx(0.0)
    assert report.profit_factor is None


def test_gross_profit_zero_profit_factor_zero() -> None:
    series = _series([10.0, 10.0])
    result = _result([100.0, 90.0], trades=(_trade(-10.0),))
    report = analyze_performance(result, series)
    assert report.gross_profit == pytest.approx(0.0)
    assert report.gross_loss == pytest.approx(10.0)
    assert report.profit_factor == pytest.approx(0.0)


def test_drawdown_depth() -> None:
    series = _series([10.0, 10.0, 10.0, 10.0])
    result = _result([100.0, 120.0, 90.0, 100.0])
    report = analyze_performance(result, series)
    assert report.max_drawdown == pytest.approx(30.0)
    assert report.max_drawdown_pct == pytest.approx(30.0 / 120.0)


def test_drawdown_preserved_after_recovery() -> None:
    series = _series([10.0, 10.0, 10.0, 10.0, 10.0])
    result = _result([100.0, 150.0, 75.0, 200.0, 180.0])
    report = analyze_performance(result, series)
    assert report.max_drawdown == pytest.approx(75.0)
    assert report.max_drawdown_pct == pytest.approx(0.5)


def test_flat_equity_zero_drawdown() -> None:
    series = _series([10.0, 11.0, 12.0])
    result = _result([100.0, 100.0, 100.0])
    report = analyze_performance(result, series)
    assert report.max_drawdown == pytest.approx(0.0)
    assert report.max_drawdown_pct == pytest.approx(0.0)
    assert report.total_return == pytest.approx(0.0)


def test_benchmark_uses_first_and_last_close() -> None:
    series = _series([100.0, 110.0, 120.0])
    result = _result([10_000.0, 10_000.0, 9_000.0])
    report = analyze_performance(result, series)
    assert report.benchmark_return == pytest.approx(120.0 / 100.0 - 1.0)
    assert report.total_return == pytest.approx(-0.10)


def test_exposure_flat_long_and_partial() -> None:
    series = _series([10.0, 10.0, 10.0, 10.0])
    flat = analyze_performance(_result([100.0, 100.0, 100.0, 100.0]), series)
    assert flat.exposure == pytest.approx(0.0)
    long = analyze_performance(
        _result([100.0, 100.0, 100.0, 100.0], quantities=[1.0, 1.0, 1.0, 1.0]),
        series,
    )
    assert long.exposure == pytest.approx(1.0)
    partial = analyze_performance(
        _result([100.0, 100.0, 100.0, 100.0], quantities=[0.0, 1.0, 1.0, 0.0]),
        series,
    )
    assert partial.exposure == pytest.approx(0.5)


def test_analyze_performance_is_deterministic() -> None:
    series = _series([10.0, 12.0, 8.0, 9.0])
    result = _result(
        [100.0, 110.0, 80.0, 90.0],
        quantities=[0.0, 1.0, 1.0, 0.0],
        trades=(_trade(10.0), _trade(-5.0)),
    )
    assert analyze_performance(result, series) == analyze_performance(result, series)


def test_equity_length_mismatch_fails() -> None:
    series = _series([10.0, 11.0, 12.0])
    result = _result([100.0, 100.0])
    with pytest.raises(ValueError, match="length"):
        analyze_performance(result, series)


def test_equity_timestamp_mismatch_fails() -> None:
    series = _series([10.0, 11.0])
    result = _result([100.0, 100.0])
    bad_points = (
        result.equity[0],
        EquityPoint(
            timestamp=99,
            cash=100.0,
            quantity=0.0,
            position_value=0.0,
            equity=100.0,
        ),
    )
    bad = BacktestResult(
        trades=(),
        equity=bad_points,
        final_equity=100.0,
        final_cash=100.0,
        final_quantity=0.0,
        unfilled_signal=None,
        metadata={},
    )
    with pytest.raises(ValueError, match="timestamp"):
        analyze_performance(bad, series)


def test_initial_equity_zero_fails() -> None:
    series = _series([10.0, 11.0])
    result = _result([0.0, 10.0])
    with pytest.raises(ValueError, match="initial equity"):
        analyze_performance(result, series)

import pytest

from tradingbot.data.ohlcv.models import Candle
from tradingbot.portfolio_sim.engine import run_portfolio_sim
from tradingbot.portfolio_sim.models import (
    CompletedTrade,
    PortfolioEquityPoint,
    PortfolioSimConfig,
    PortfolioSimResult,
)
from tradingbot.portfolio_sim.performance import (
    MAX_DRAWDOWN_SOURCE_SIMULATED_CURVE,
    STALE_MARK_SCOPE_LAST_BAR_ONLY,
    analyze_portfolio,
)
from tradingbot.strategy.ma_crossover import MACrossover, MACrossoverParams
from tradingbot.strategy.models import CandleSeries

TF = 14_400_000


def _series(symbol: str, rows: list[tuple[float, float]]) -> CandleSeries:
    candles = [
        Candle(
            timestamp=i * TF,
            open=open_,
            high=max(open_, close),
            low=min(open_, close),
            close=close,
            volume=1.0,
        )
        for i, (open_, close) in enumerate(rows)
    ]
    return CandleSeries.from_closed(
        symbol=symbol,
        timeframe="4h",
        candles=candles,
        now_ms=candles[-1].timestamp + 2 * TF,
    )


def test_performance_mtm_and_empty_trades() -> None:
    series = _series("AAA/USDT", [(10.0, 10.0), (20.0, 25.0), (26.0, 40.0)])
    result = run_portfolio_sim(
        series_by_symbol={"AAA/USDT": series},
        symbol_order=("AAA/USDT",),
        config=PortfolioSimConfig(
            initial_cash=10_000.0,
            commission_rate=0.0,
            net_profit_multiple=100.0,
        ),
        rank_fn=lambda prefixes, _order: tuple(prefixes),
        ma_strategy=MACrossover(MACrossoverParams(2, 3)),
    )
    report = analyze_portfolio(result)
    qty = 10_000.0 / 20.0
    assert report.trade_count == 0
    assert report.mean_trade_pnl is None
    assert report.win_rate is None
    assert report.profit_factor is None
    assert report.average_winning_trade_pnl is None
    assert report.average_losing_trade_pnl is None
    assert report.winning_trade_count == 0
    assert report.losing_trade_count == 0
    assert report.scratch_trade_count == 0
    assert report.open_mark_value == pytest.approx(qty * 40.0)
    assert report.unrealized_pnl == pytest.approx(qty * 40.0 - 10_000.0)
    assert report.total_return == pytest.approx((qty * 40.0 - 10_000.0) / 10_000.0)
    assert "survivorship" in report.survivorship_warning
    assert "scenario" in report.slippage_label.lower()
    assert report.hypothetical_liquidation_net == pytest.approx(qty * 40.0)


def test_performance_completed_trade_metrics() -> None:
    series = _series(
        "AAA/USDT",
        [(10.0, 11.0), (12.0, 13.0), (8.0, 8.0), (9.0, 9.0), (9.0, 9.0)],
    )
    result = run_portfolio_sim(
        series_by_symbol={"AAA/USDT": series},
        symbol_order=("AAA/USDT",),
        config=PortfolioSimConfig(
            initial_cash=10_000.0,
            commission_rate=0.0,
            net_profit_multiple=100.0,
        ),
        rank_fn=lambda prefixes, _order: tuple(prefixes),
        ma_strategy=MACrossover(MACrossoverParams(2, 3)),
    )
    report = analyze_portfolio(result)
    assert report.trade_count >= 1
    assert report.mean_hold_ms is not None
    assert report.max_concurrent_open >= 1
    assert report.total_commission == pytest.approx(0.0)


def test_ended_series_marked_stale_not_flattened() -> None:
    held = _series("AAA/USDT", [(10.0, 10.0), (11.0, 11.0), (12.0, 12.0)])
    cont = _series("BBB/USDT", [(9.0, 9.0)] * 8)
    result = run_portfolio_sim(
        series_by_symbol={"AAA/USDT": held, "BBB/USDT": cont},
        symbol_order=("AAA/USDT", "BBB/USDT"),
        config=PortfolioSimConfig(
            initial_cash=10_000.0,
            commission_rate=0.0,
            net_profit_multiple=100.0,
        ),
        rank_fn=lambda prefixes, _order: tuple(
            name for name in ("AAA/USDT",) if name in prefixes
        ),
        ma_strategy=MACrossover(MACrossoverParams(2, 3)),
    )
    assert result.trades == ()
    assert result.open_positions[0].symbol == "AAA/USDT"
    assert "AAA/USDT" in result.stale_mark_symbols
    report = analyze_portfolio(result)
    assert report.stale_open_mark_value == pytest.approx(report.open_mark_value)
    assert report.open_marks[0].stale is True
    assert "last known" in report.stale_mark_warning
    assert report.stale_mark_symbols == result.stale_mark_symbols
    assert report.stale_mark_scope == STALE_MARK_SCOPE_LAST_BAR_ONLY
    qty = 10_000.0 / 11.0
    assert report.open_mark_value == pytest.approx(qty * 12.0)


def test_missing_mark_price_is_not_fabricated() -> None:
    series = _series("AAA/USDT", [(10.0, 10.0), (20.0, 25.0), (26.0, 40.0)])
    result = run_portfolio_sim(
        series_by_symbol={"AAA/USDT": series},
        symbol_order=("AAA/USDT",),
        config=PortfolioSimConfig(
            initial_cash=10_000.0,
            commission_rate=0.0,
            net_profit_multiple=100.0,
        ),
        rank_fn=lambda prefixes, _order: tuple(prefixes),
        ma_strategy=MACrossover(MACrossoverParams(2, 3)),
    )
    assert result.open_positions
    assert result.trades == ()
    with pytest.raises(ValueError, match="not fabricated"):
        analyze_portfolio(result, mark_prices={})


def test_mark_prices_align_equity_and_unrealized() -> None:
    series = _series("AAA/USDT", [(10.0, 10.0), (20.0, 25.0), (26.0, 40.0)])
    result = run_portfolio_sim(
        series_by_symbol={"AAA/USDT": series},
        symbol_order=("AAA/USDT",),
        config=PortfolioSimConfig(
            initial_cash=10_000.0,
            commission_rate=0.0,
            net_profit_multiple=100.0,
        ),
        rank_fn=lambda prefixes, _order: tuple(prefixes),
        ma_strategy=MACrossover(MACrossoverParams(2, 3)),
    )
    qty = result.open_positions[0].quantity
    entry = result.open_positions[0].entry_cost
    override = 50.0
    report = analyze_portfolio(result, mark_prices={"AAA/USDT": override})
    assert report.open_mark_value == pytest.approx(qty * override)
    assert report.unrealized_pnl == pytest.approx(qty * override - entry)
    assert report.final_equity == pytest.approx(result.final_cash + report.open_mark_value)
    assert report.total_return == pytest.approx(
        (report.final_equity - 10_000.0) / 10_000.0
    )
    default = analyze_portfolio(result)
    assert default.final_equity == pytest.approx(result.final_cash + default.open_mark_value)
    assert default.unrealized_pnl == pytest.approx(default.open_mark_value - entry)
    assert report.max_drawdown == pytest.approx(default.max_drawdown)
    assert report.max_drawdown_pct == pytest.approx(default.max_drawdown_pct)
    assert report.max_drawdown_source == MAX_DRAWDOWN_SOURCE_SIMULATED_CURVE
    assert default.max_drawdown_source == MAX_DRAWDOWN_SOURCE_SIMULATED_CURVE
    assert report.final_equity != pytest.approx(default.final_equity)


def test_open_entry_slippage_not_in_completed_total() -> None:
    series = _series("AAA/USDT", [(10.0, 10.0), (100.0, 100.0), (100.0, 100.0)])
    result = run_portfolio_sim(
        series_by_symbol={"AAA/USDT": series},
        symbol_order=("AAA/USDT",),
        config=PortfolioSimConfig(
            initial_cash=10_000.0,
            commission_rate=0.0,
            slippage=0.001,
            net_profit_multiple=100.0,
        ),
        rank_fn=lambda prefixes, _order: tuple(prefixes),
        ma_strategy=MACrossover(MACrossoverParams(2, 3)),
    )
    report = analyze_portfolio(result)
    assert result.trades == ()
    assert report.total_slippage_cost == pytest.approx(0.0)
    assert report.open_entry_slippage_cost > 0.0
    pos = result.open_positions[0]
    assert report.open_entry_slippage_cost == pytest.approx(
        pos.quantity * (pos.buy_fill - pos.reference_buy)
    )


def _trade(pnl: float) -> CompletedTrade:
    return CompletedTrade(
        symbol="AAA/USDT",
        entry_timestamp=0,
        exit_timestamp=TF,
        buy_fill=10.0,
        sell_fill=10.0,
        reference_buy=10.0,
        reference_sell=10.0,
        quantity=1.0,
        entry_fee=0.0,
        exit_fee=0.0,
        pnl=pnl,
        return_pct=0.0,
        exit_reason="ma_sell",
        slippage_cost=0.0,
    )


def _result_from_trades(
    trades: tuple[CompletedTrade, ...],
    *,
    equities: tuple[float, ...] = (10_000.0, 9_000.0, 9_500.0),
) -> PortfolioSimResult:
    points = tuple(
        PortfolioEquityPoint(
            timestamp=i * TF,
            cash=value,
            position_value=0.0,
            equity=value,
            open_count=0,
            cash_ratio=1.0,
        )
        for i, value in enumerate(equities)
    )
    return PortfolioSimResult(
        trades=trades,
        equity=points,
        open_positions=(),
        last_marks={},
        stale_mark_symbols=(),
        final_cash=points[-1].cash,
        final_equity=points[-1].equity,
        config=PortfolioSimConfig(initial_cash=10_000.0, commission_rate=0.0),
        metadata={},
    )


def test_profit_factor_and_average_pnl_mixed_trades() -> None:
    result = _result_from_trades((_trade(10.0), _trade(20.0), _trade(-5.0), _trade(-15.0)))
    report = analyze_portfolio(result)
    assert report.winning_trade_count == 2
    assert report.losing_trade_count == 2
    assert report.scratch_trade_count == 0
    assert report.average_winning_trade_pnl == pytest.approx(15.0)
    assert report.average_losing_trade_pnl == pytest.approx(-10.0)
    assert report.profit_factor == pytest.approx(30.0 / 20.0)
    assert report.win_rate == pytest.approx(0.5)
    assert report.mean_trade_pnl == pytest.approx(10.0 / 4.0)


def test_profit_factor_undefined_when_no_losing_trades() -> None:
    result = _result_from_trades((_trade(4.0), _trade(6.0)))
    report = analyze_portfolio(result)
    assert report.losing_trade_count == 0
    assert report.profit_factor is None
    assert report.average_winning_trade_pnl == pytest.approx(5.0)
    assert report.average_losing_trade_pnl is None


def test_average_win_undefined_when_only_losing_trades() -> None:
    result = _result_from_trades((_trade(-3.0), _trade(-7.0)))
    report = analyze_portfolio(result)
    assert report.winning_trade_count == 0
    assert report.average_winning_trade_pnl is None
    assert report.average_losing_trade_pnl == pytest.approx(-5.0)
    assert report.profit_factor == pytest.approx(0.0)


def test_trade_metrics_undefined_when_no_completed_trades() -> None:
    result = _result_from_trades(())
    report = analyze_portfolio(result)
    assert report.trade_count == 0
    assert report.profit_factor is None
    assert report.average_winning_trade_pnl is None
    assert report.average_losing_trade_pnl is None
    assert report.winning_trade_count == 0
    assert report.losing_trade_count == 0
    assert report.scratch_trade_count == 0
    assert report.win_rate is None
    assert report.mean_trade_pnl is None


def test_scratch_pnl_excluded_from_win_loss_groups() -> None:
    result = _result_from_trades((_trade(8.0), _trade(0.0), _trade(-2.0)))
    report = analyze_portfolio(result)
    assert report.trade_count == 3
    assert report.winning_trade_count == 1
    assert report.losing_trade_count == 1
    assert report.scratch_trade_count == 1
    assert report.average_winning_trade_pnl == pytest.approx(8.0)
    assert report.average_losing_trade_pnl == pytest.approx(-2.0)
    assert report.profit_factor == pytest.approx(8.0 / 2.0)
    assert report.win_rate == pytest.approx(1.0 / 3.0)
    assert report.mean_trade_pnl == pytest.approx(6.0 / 3.0)


def test_intra_curve_stale_marks_not_invented() -> None:
    result = _result_from_trades(())
    report = analyze_portfolio(result)
    assert report.stale_mark_scope == STALE_MARK_SCOPE_LAST_BAR_ONLY
    assert report.stale_mark_symbols == ()
    assert report.max_drawdown_source == MAX_DRAWDOWN_SOURCE_SIMULATED_CURVE

from collections.abc import Mapping, Sequence

import pytest

from tradingbot.data.ohlcv.models import Candle
from tradingbot.portfolio_sim.engine import run_portfolio_sim
from tradingbot.portfolio_sim.exits import (
    buy_fill_price,
    max_sell_fill_for_stop,
    min_sell_fill_for_target,
)
from tradingbot.portfolio_sim.models import PortfolioSimConfig
from tradingbot.portfolio_sim.performance import analyze_portfolio
from tradingbot.strategy.ma_crossover import MACrossover, MACrossoverParams
from tradingbot.strategy.models import CandleSeries, Signal

TF = 14_400_000


def _bar(i: int, open_: float, close: float, *, high: float | None = None, low: float | None = None) -> Candle:
    hi = high if high is not None else max(open_, close)
    lo = low if low is not None else min(open_, close)
    return Candle(timestamp=i * TF, open=open_, high=hi, low=lo, close=close, volume=1.0)


def _series(symbol: str, rows: list[tuple[float, float]]) -> CandleSeries:
    candles = [_bar(i, open_, close) for i, (open_, close) in enumerate(rows)]
    return CandleSeries.from_closed(
        symbol=symbol,
        timeframe="4h",
        candles=candles,
        now_ms=candles[-1].timestamp + 2 * TF,
    )


def _cfg(**kwargs: float | int | None) -> PortfolioSimConfig:
    payload: dict[str, float | int | None] = {
        "initial_cash": 10_000.0,
        "commission_rate": 0.0,
        "net_profit_multiple": None,
        "stop_loss_multiple": None,
    }
    payload.update(kwargs)
    npm = payload["net_profit_multiple"]
    slm = payload["stop_loss_multiple"]
    return PortfolioSimConfig(
        initial_cash=float(payload["initial_cash"]),
        commission_rate=float(payload["commission_rate"]),
        slippage=float(payload.get("slippage", 0.0)),
        max_positions=int(payload.get("max_positions", 5)),
        net_profit_multiple=None if npm is None else float(npm),
        stop_loss_multiple=None if slm is None else float(slm),
    )


def _rank_fixed(
    names: tuple[str, ...],
) -> callable:
    def _fn(
        prefixes: Mapping[str, CandleSeries], _order: Sequence[str]
    ) -> tuple[str, ...]:
        return tuple(name for name in names if name in prefixes)

    return _fn


def test_future_bars_not_in_decision_prefixes() -> None:
    seen: list[int] = []
    series = _series("AAA/USDT", [(10.0, 10.0), (11.0, 11.0), (99.0, 99.0)])

    def rank_fn(prefixes: Mapping[str, CandleSeries], _order: Sequence[str]) -> tuple[str, ...]:
        prefix = prefixes["AAA/USDT"]
        seen.append(prefix.candles[-1].timestamp)
        for candle in prefix.candles:
            assert candle.timestamp <= seen[-1]
        return ("AAA/USDT",)

    run_portfolio_sim(
        series_by_symbol={"AAA/USDT": series},
        symbol_order=("AAA/USDT",),
        config=_cfg(),
        rank_fn=rank_fn,
        ma_strategy=MACrossover(MACrossoverParams(2, 3)),
    )
    assert seen
    assert max(seen) == TF


def test_fill_is_next_open_not_signal_close() -> None:
    series = _series("AAA/USDT", [(10.0, 999.0), (20.0, 21.0), (22.0, 23.0)])
    result = run_portfolio_sim(
        series_by_symbol={"AAA/USDT": series},
        symbol_order=("AAA/USDT",),
        config=_cfg(),
        rank_fn=_rank_fixed(("AAA/USDT",)),
        ma_strategy=MACrossover(MACrossoverParams(2, 3)),
    )
    pos = result.open_positions[0]
    assert pos.buy_fill == pytest.approx(20.0)
    assert pos.quantity == pytest.approx(10_000.0 / 20.0)
    assert pos.quantity != pytest.approx(10_000.0 / 999.0)


class _SellAt:
    """Test double: emit one MA sell at a closed-bar timestamp."""

    def __init__(self, symbol: str, timestamp: int) -> None:
        self.symbol = symbol
        self.timestamp = timestamp

    def generate(self, series: CandleSeries) -> list[Signal]:
        if series.symbol != self.symbol or not series.candles:
            return []
        last = series.candles[-1]
        if last.timestamp != self.timestamp:
            return []
        return [
            Signal(
                symbol=series.symbol,
                timeframe=series.timeframe,
                timestamp=self.timestamp,
                side="sell",
                strategy="scripted",
                reason="test",
            )
        ]


def test_pending_ma_gap_does_not_exceed_max_positions() -> None:
    held = tuple(f"S{i}/USDT" for i in range(5))
    challenger = "S5/USDT"
    names = (*held, challenger)
    sell_ts = 2 * TF
    series_map: dict[str, CandleSeries] = {}
    for name in held:
        if name == "S0/USDT":
            candles = (
                _bar(0, 10.0, 10.0),
                _bar(1, 10.0, 10.0),
                _bar(2, 10.0, 10.0),
                _bar(4, 10.0, 10.0),
                _bar(5, 10.0, 10.0),
            )
        else:
            candles = tuple(_bar(i, 10.0, 10.0) for i in range(6))
        series_map[name] = CandleSeries.from_closed(
            symbol=name,
            timeframe="4h",
            candles=candles,
            now_ms=candles[-1].timestamp + 2 * TF,
        )
    series_map[challenger] = CandleSeries.from_closed(
        symbol=challenger,
        timeframe="4h",
        candles=tuple(_bar(i, 10.0, 10.0) for i in range(6)),
        now_ms=5 * TF + 2 * TF,
    )
    result = run_portfolio_sim(
        series_by_symbol=series_map,
        symbol_order=names,
        config=_cfg(max_positions=5),
        rank_fn=_rank_fixed(names),
        ma_strategy=_SellAt("S0/USDT", sell_ts),
    )
    assert all(point.open_count <= 5 for point in result.equity)
    assert int(result.metadata["max_open_positions"]) <= 5
    assert len(result.open_positions) <= 5
    gap_ts = 3 * TF
    gap_point = next(point for point in result.equity if point.timestamp == gap_ts)
    assert gap_point.open_count == 5


def test_filled_exit_frees_slot_for_new_buy() -> None:
    held = tuple(f"S{i}/USDT" for i in range(5))
    challenger = "S5/USDT"
    names = (*held, challenger)
    sell_ts = 2 * TF
    rows = [(10.0, 10.0)] * 6
    series_map = {name: _series(name, rows) for name in names}

    def rank_fn(prefixes: Mapping[str, CandleSeries], _order: Sequence[str]) -> tuple[str, ...]:
        last = max(item.candles[-1].timestamp for item in prefixes.values())
        if last <= sell_ts:
            preferred = held
        else:
            preferred = (*held[1:], challenger)
        return tuple(name for name in preferred if name in prefixes)

    result = run_portfolio_sim(
        series_by_symbol=series_map,
        symbol_order=names,
        config=_cfg(max_positions=5),
        rank_fn=rank_fn,
        ma_strategy=_SellAt("S0/USDT", sell_ts),
    )
    assert all(point.open_count <= 5 for point in result.equity)
    assert any(item.symbol == "S0/USDT" and item.exit_reason == "ma_sell" for item in result.trades)
    held_names = {item.symbol for item in result.open_positions}
    assert challenger in held_names
    assert "S0/USDT" not in held_names
    assert len(result.open_positions) == 5


def test_five_open_blocks_new_buy() -> None:
    names = tuple(f"S{i}/USDT" for i in range(6))
    rows = [(10.0, 10.0), (10.0, 10.0), (10.0, 10.0), (10.0, 10.0)]
    series_map = {name: _series(name, rows) for name in names}
    result = run_portfolio_sim(
        series_by_symbol=series_map,
        symbol_order=names,
        config=_cfg(max_positions=5),
        rank_fn=_rank_fixed(names),
        ma_strategy=MACrossover(MACrossoverParams(2, 3)),
    )
    held = {item.symbol for item in result.open_positions}
    assert len(held) == 5
    assert "S5/USDT" not in held
    assert all(item.symbol != "S5/USDT" for item in result.trades)


def test_zero_candidates_keeps_cash() -> None:
    series = _series("AAA/USDT", [(10.0, 10.0), (11.0, 11.0)])
    result = run_portfolio_sim(
        series_by_symbol={"AAA/USDT": series},
        symbol_order=("AAA/USDT",),
        config=_cfg(),
        rank_fn=_rank_fixed(()),
        ma_strategy=MACrossover(MACrossoverParams(2, 3)),
    )
    assert result.open_positions == ()
    assert result.trades == ()
    assert result.final_cash == pytest.approx(10_000.0)


def test_two_candidates_open_at_most_two() -> None:
    names = ("A/USDT", "B/USDT", "C/USDT")
    rows = [(10.0, 10.0), (10.0, 10.0), (10.0, 10.0)]
    series_map = {name: _series(name, rows) for name in names}
    result = run_portfolio_sim(
        series_by_symbol=series_map,
        symbol_order=names,
        config=_cfg(),
        rank_fn=_rank_fixed(("A/USDT", "B/USDT")),
        ma_strategy=MACrossover(MACrossoverParams(2, 3)),
    )
    held = {item.symbol for item in result.open_positions}
    assert held == {"A/USDT", "B/USDT"}


def test_open_symbol_not_bought_again() -> None:
    series = _series("AAA/USDT", [(10.0, 10.0), (11.0, 11.0), (12.0, 12.0), (13.0, 13.0)])
    result = run_portfolio_sim(
        series_by_symbol={"AAA/USDT": series},
        symbol_order=("AAA/USDT",),
        config=_cfg(),
        rank_fn=_rank_fixed(("AAA/USDT",)),
        ma_strategy=MACrossover(MACrossoverParams(2, 3)),
    )
    assert len(result.open_positions) == 1
    assert result.open_positions[0].quantity == pytest.approx(10_000.0 / 11.0)


def test_exits_before_buys_on_same_open() -> None:
    a = _series(
        "A/USDT",
        [(10.0, 11.0), (12.0, 13.0), (8.0, 8.0), (9.0, 9.0), (9.0, 9.0)],
    )
    b = _series(
        "B/USDT",
        [(10.0, 10.0), (10.0, 10.0), (10.0, 10.0), (40.0, 40.0), (40.0, 40.0)],
    )

    def rank_fn(prefixes: Mapping[str, CandleSeries], _order: Sequence[str]) -> tuple[str, ...]:
        last = max(item.candles[-1].timestamp for item in prefixes.values())
        if last == 0:
            return ("A/USDT",)
        return ("B/USDT",)

    result = run_portfolio_sim(
        series_by_symbol={"A/USDT": a, "B/USDT": b},
        symbol_order=("A/USDT", "B/USDT"),
        config=_cfg(),
        rank_fn=rank_fn,
        ma_strategy=MACrossover(MACrossoverParams(2, 3)),
    )
    assert any(item.symbol == "A/USDT" and item.exit_reason == "ma_sell" for item in result.trades)
    held = {item.symbol for item in result.open_positions}
    assert "B/USDT" in held
    assert "A/USDT" not in held


def test_equal_allocation_does_not_exceed_cash() -> None:
    names = ("A/USDT", "B/USDT")
    rows = [(10.0, 10.0), (10.0, 10.0), (10.0, 10.0)]
    series_map = {name: _series(name, rows) for name in names}
    result = run_portfolio_sim(
        series_by_symbol=series_map,
        symbol_order=names,
        config=_cfg(initial_cash=100.0),
        rank_fn=_rank_fixed(names),
        ma_strategy=MACrossover(MACrossoverParams(2, 3)),
    )
    spent = sum(item.entry_cost for item in result.open_positions)
    assert spent == pytest.approx(100.0)
    assert result.final_cash == pytest.approx(0.0, abs=1e-9)
    assert all(item.entry_cost == pytest.approx(50.0) for item in result.open_positions)


def test_failed_buy_does_not_respend_cash() -> None:
    good = _series("GOOD/USDT", [(10.0, 10.0), (10.0, 10.0), (10.0, 10.0)])
    missing_next = CandleSeries.from_closed(
        symbol="GAP/USDT",
        timeframe="4h",
        candles=(_bar(0, 10.0, 10.0),),
        now_ms=2 * TF,
    )
    result = run_portfolio_sim(
        series_by_symbol={"GOOD/USDT": good, "GAP/USDT": missing_next},
        symbol_order=("GAP/USDT", "GOOD/USDT"),
        config=_cfg(initial_cash=100.0),
        rank_fn=_rank_fixed(("GAP/USDT", "GOOD/USDT")),
        ma_strategy=MACrossover(MACrossoverParams(2, 3)),
    )
    assert {item.symbol for item in result.open_positions} == {"GOOD/USDT"}
    assert result.open_positions[0].entry_cost == pytest.approx(50.0)
    assert result.final_cash == pytest.approx(50.0)


def test_insufficient_allocation_does_not_go_negative() -> None:
    series = _series("AAA/USDT", [(10.0, 10.0), (10.0, 10.0)])
    result = run_portfolio_sim(
        series_by_symbol={"AAA/USDT": series},
        symbol_order=("AAA/USDT",),
        config=_cfg(initial_cash=1e-12, commission_rate=0.001),
        rank_fn=_rank_fixed(("AAA/USDT",)),
        ma_strategy=MACrossover(MACrossoverParams(2, 3)),
    )
    assert result.final_cash >= 0.0
    assert all(point.cash >= -1e-12 for point in result.equity)


def test_entry_and_exit_commission() -> None:
    r = 0.001
    series = _series(
        "AAA/USDT",
        [(10.0, 11.0), (100.0, 101.0), (8.0, 8.0), (110.0, 110.0), (110.0, 110.0)],
    )
    result = run_portfolio_sim(
        series_by_symbol={"AAA/USDT": series},
        symbol_order=("AAA/USDT",),
        config=_cfg(commission_rate=r),
        rank_fn=_rank_fixed(("AAA/USDT",)),
        ma_strategy=MACrossover(MACrossoverParams(2, 3)),
    )
    trade = result.trades[0]
    qty = 10_000.0 / (100.0 * (1.0 + r))
    assert trade.quantity == pytest.approx(qty)
    assert trade.entry_fee == pytest.approx(qty * 100.0 * r)
    assert trade.exit_fee == pytest.approx(qty * 110.0 * r)


def test_slippage_direction() -> None:
    s = 0.001
    series = _series("AAA/USDT", [(10.0, 10.0), (100.0, 100.0), (100.0, 100.0)])
    result = run_portfolio_sim(
        series_by_symbol={"AAA/USDT": series},
        symbol_order=("AAA/USDT",),
        config=_cfg(slippage=s),
        rank_fn=_rank_fixed(("AAA/USDT",)),
        ma_strategy=MACrossover(MACrossoverParams(2, 3)),
    )
    pos = result.open_positions[0]
    assert pos.buy_fill == pytest.approx(buy_fill_price(100.0, s))
    assert pos.buy_fill > 100.0


def test_ma_sell_fills_next_open() -> None:
    series = _series(
        "AAA/USDT",
        [(10.0, 11.0), (12.0, 13.0), (8.0, 8.0), (9.0, 9.0), (9.0, 9.0)],
    )
    result = run_portfolio_sim(
        series_by_symbol={"AAA/USDT": series},
        symbol_order=("AAA/USDT",),
        config=_cfg(),
        rank_fn=_rank_fixed(("AAA/USDT",)),
        ma_strategy=MACrossover(MACrossoverParams(2, 3)),
    )
    assert result.trades[0].exit_reason == "ma_sell"
    assert result.trades[0].reference_sell == pytest.approx(9.0)
    assert result.trades[0].exit_timestamp == 3 * TF


def test_high_alone_does_not_fill_take_profit() -> None:
    series = _series(
        "AAA/USDT",
        [(10.0, 10.0), (100.0, 100.0), (100.0, 100.0), (100.0, 100.0)],
    )
    candles = list(series.candles)
    candles[2] = _bar(2, 100.0, 100.0, high=10_000.0, low=99.0)
    series = CandleSeries.from_closed(
        symbol="AAA/USDT",
        timeframe="4h",
        candles=candles,
        now_ms=candles[-1].timestamp + 2 * TF,
    )
    result = run_portfolio_sim(
        series_by_symbol={"AAA/USDT": series},
        symbol_order=("AAA/USDT",),
        config=_cfg(net_profit_multiple=1.01),
        rank_fn=_rank_fixed(("AAA/USDT",)),
        ma_strategy=MACrossover(MACrossoverParams(2, 3)),
    )
    assert all(item.exit_reason != "take_profit" for item in result.trades)
    assert result.open_positions


def test_gap_open_does_not_fill_at_target_price() -> None:
    r = 0.0
    s = 0.0
    buy_open = 100.0
    min_sell = min_sell_fill_for_target(buy_fill=buy_open, commission_rate=r)
    gap = min_sell * 3.0
    series = _series(
        "AAA/USDT",
        [(10.0, 10.0), (buy_open, buy_open), (gap, gap), (gap, gap)],
    )
    result = run_portfolio_sim(
        series_by_symbol={"AAA/USDT": series},
        symbol_order=("AAA/USDT",),
        config=_cfg(net_profit_multiple=1.01),
        rank_fn=_rank_fixed(("AAA/USDT",)),
        ma_strategy=MACrossover(MACrossoverParams(2, 3)),
    )
    tp = [item for item in result.trades if item.exit_reason == "take_profit"]
    assert tp
    assert tp[0].sell_fill == pytest.approx(gap)
    assert tp[0].sell_fill != pytest.approx(min_sell)


def test_target_and_ma_same_open_single_exit() -> None:
    series = _series(
        "AAA/USDT",
        [(10.0, 11.0), (12.0, 13.0), (8.0, 8.0), (50.0, 50.0), (50.0, 50.0)],
    )
    result = run_portfolio_sim(
        series_by_symbol={"AAA/USDT": series},
        symbol_order=("AAA/USDT",),
        config=_cfg(net_profit_multiple=1.01),
        rank_fn=_rank_fixed(("AAA/USDT",)),
        ma_strategy=MACrossover(MACrossoverParams(2, 3)),
    )
    exits = [item for item in result.trades if item.symbol == "AAA/USDT"]
    assert len(exits) == 1
    assert exits[0].exit_reason == "ma_sell"
    assert exits[0].sell_fill == pytest.approx(50.0)


def test_open_position_mtm_not_force_closed() -> None:
    series = _series("AAA/USDT", [(10.0, 10.0), (20.0, 25.0), (26.0, 40.0)])
    result = run_portfolio_sim(
        series_by_symbol={"AAA/USDT": series},
        symbol_order=("AAA/USDT",),
        config=_cfg(),
        rank_fn=_rank_fixed(("AAA/USDT",)),
        ma_strategy=MACrossover(MACrossoverParams(2, 3)),
    )
    assert result.trades == ()
    assert len(result.open_positions) == 1
    qty = 10_000.0 / 20.0
    assert result.open_positions[0].quantity == pytest.approx(qty)
    assert result.final_equity == pytest.approx(qty * 40.0)
    report = analyze_portfolio(result)
    assert report.open_mark_value == pytest.approx(qty * 40.0)
    assert report.trade_count == 0


def test_deterministic() -> None:
    names = ("A/USDT", "B/USDT")
    rows = [(10.0, 10.5), (11.0, 11.5), (8.0, 8.0), (9.0, 9.0)]
    series_map = {name: _series(name, rows) for name in names}
    kwargs = dict(
        series_by_symbol=series_map,
        symbol_order=names,
        config=_cfg(commission_rate=0.001, slippage=0.0002),
        rank_fn=_rank_fixed(names),
        ma_strategy=MACrossover(MACrossoverParams(2, 3)),
    )
    first = run_portfolio_sim(**kwargs)
    second = run_portfolio_sim(**kwargs)
    assert first.trades == second.trades
    assert first.final_equity == second.final_equity
    assert first.open_positions == second.open_positions


def test_insufficient_symbol_does_not_block_others() -> None:
    good = _series("GOOD/USDT", [(10.0, 10.0), (11.0, 11.0), (12.0, 12.0)])
    short = _series("BAD/USDT", [(10.0, 10.0)])
    result = run_portfolio_sim(
        series_by_symbol={"GOOD/USDT": good, "BAD/USDT": short},
        symbol_order=("BAD/USDT", "GOOD/USDT"),
        config=_cfg(),
        rank_fn=_rank_fixed(("GOOD/USDT",)),
        ma_strategy=MACrossover(MACrossoverParams(2, 3)),
    )
    assert {item.symbol for item in result.open_positions} == {"GOOD/USDT"}


def test_ma_exit_survives_missing_next_open() -> None:
    candles = (
        _bar(0, 10.0, 11.0),
        _bar(1, 12.0, 13.0),
        _bar(2, 8.0, 8.0),
        _bar(4, 7.0, 7.0),
        _bar(5, 7.0, 7.0),
    )
    series = CandleSeries.from_closed(
        symbol="AAA/USDT",
        timeframe="4h",
        candles=candles,
        now_ms=candles[-1].timestamp + 2 * TF,
    )
    filler = _series(
        "BBB/USDT",
        [(10.0, 10.0), (10.0, 10.0), (10.0, 10.0), (10.0, 10.0), (10.0, 10.0), (10.0, 10.0)],
    )
    result = run_portfolio_sim(
        series_by_symbol={"AAA/USDT": series, "BBB/USDT": filler},
        symbol_order=("AAA/USDT", "BBB/USDT"),
        config=_cfg(),
        rank_fn=_rank_fixed(("AAA/USDT",)),
        ma_strategy=MACrossover(MACrossoverParams(2, 3)),
    )
    assert result.trades[0].exit_reason == "ma_sell"
    assert result.trades[0].exit_timestamp == 4 * TF
    assert result.trades[0].reference_sell == pytest.approx(7.0)
    assert result.trades[0].reference_sell != pytest.approx(8.0)


def test_ma_exit_not_applied_to_signal_close() -> None:
    series = _series(
        "AAA/USDT",
        [(10.0, 11.0), (12.0, 13.0), (8.0, 8.0), (9.0, 9.0), (9.0, 9.0)],
    )
    result = run_portfolio_sim(
        series_by_symbol={"AAA/USDT": series},
        symbol_order=("AAA/USDT",),
        config=_cfg(),
        rank_fn=_rank_fixed(("AAA/USDT",)),
        ma_strategy=MACrossover(MACrossoverParams(2, 3)),
    )
    assert result.trades[0].exit_reason == "ma_sell"
    assert result.trades[0].reference_sell == pytest.approx(9.0)
    assert result.trades[0].reference_sell != pytest.approx(8.0)
    assert result.trades[0].exit_timestamp == 3 * TF


def test_duplicate_ma_pending_does_not_double_sell() -> None:
    from tradingbot.portfolio_sim.engine import _merge_pending_exits
    from tradingbot.portfolio_sim.models import OpenPosition

    position = OpenPosition(
        symbol="AAA/USDT",
        quantity=1.0,
        entry_timestamp=TF,
        buy_fill=10.0,
        reference_buy=10.0,
        entry_fee=0.0,
        entry_cost=10.0,
    )
    merged = _merge_pending_exits(
        (("AAA/USDT", "ma_sell", TF),),
        (("AAA/USDT", "ma_sell", TF), ("AAA/USDT", "ma_sell", TF)),
        {"AAA/USDT": position},
    )
    assert merged == [("AAA/USDT", "ma_sell", TF)]


def test_stale_pending_does_not_sell_new_position() -> None:
    from tradingbot.portfolio_sim.engine import _merge_pending_exits
    from tradingbot.portfolio_sim.models import OpenPosition

    new_position = OpenPosition(
        symbol="AAA/USDT",
        quantity=1.0,
        entry_timestamp=4 * TF,
        buy_fill=10.0,
        reference_buy=10.0,
        entry_fee=0.0,
        entry_cost=10.0,
    )
    merged = _merge_pending_exits(
        (("AAA/USDT", "ma_sell", TF),),
        (),
        {"AAA/USDT": new_position},
    )
    assert merged == []
    closed = _merge_pending_exits((("AAA/USDT", "ma_sell", TF),), (), {})
    assert closed == []


def test_rebought_symbol_not_sold_by_old_ma_pending() -> None:
    series = _series(
        "AAA/USDT",
        [(10.0, 11.0), (12.0, 13.0), (8.0, 8.0), (9.0, 20.0), (21.0, 21.0), (21.0, 21.0)],
    )
    result = run_portfolio_sim(
        series_by_symbol={"AAA/USDT": series},
        symbol_order=("AAA/USDT",),
        config=_cfg(),
        rank_fn=_rank_fixed(("AAA/USDT",)),
        ma_strategy=MACrossover(MACrossoverParams(2, 3)),
    )
    first = result.trades[0]
    assert first.exit_reason == "ma_sell"
    assert first.entry_timestamp == TF
    if result.open_positions:
        assert result.open_positions[0].entry_timestamp != first.entry_timestamp
    for trade in result.trades[1:]:
        assert trade.entry_timestamp != first.entry_timestamp


def test_tp_none_does_not_create_take_profit_exits() -> None:
    series = _series("AAA/USDT", [(10.0, 10.0), (100.0, 100.0), (200.0, 200.0), (200.0, 200.0)])
    result = run_portfolio_sim(
        series_by_symbol={"AAA/USDT": series},
        symbol_order=("AAA/USDT",),
        config=_cfg(net_profit_multiple=None),
        rank_fn=_rank_fixed(("AAA/USDT",)),
        ma_strategy=MACrossover(MACrossoverParams(20, 50)),
    )
    assert all(item.exit_reason != "take_profit" for item in result.trades)
    assert result.open_positions


def test_stop_loss_fills_next_open() -> None:
    series = _series("AAA/USDT", [(10.0, 10.0), (100.0, 100.0), (90.0, 90.0), (90.0, 90.0)])
    result = run_portfolio_sim(
        series_by_symbol={"AAA/USDT": series},
        symbol_order=("AAA/USDT",),
        config=_cfg(stop_loss_multiple=0.97),
        rank_fn=_rank_fixed(("AAA/USDT",)),
        ma_strategy=MACrossover(MACrossoverParams(20, 50)),
    )
    assert len(result.trades) == 1
    assert result.trades[0].exit_reason == "stop_loss"
    assert result.trades[0].reference_sell == pytest.approx(90.0)
    assert result.trades[0].sell_fill == pytest.approx(90.0)


def test_low_alone_does_not_fill_stop_loss() -> None:
    series = _series("AAA/USDT", [(10.0, 10.0), (100.0, 100.0), (100.0, 100.0), (100.0, 100.0)])
    candles = list(series.candles)
    candles[2] = _bar(2, 100.0, 100.0, high=100.0, low=1.0)
    series = CandleSeries.from_closed(
        symbol="AAA/USDT",
        timeframe="4h",
        candles=candles,
        now_ms=candles[-1].timestamp + 2 * TF,
    )
    result = run_portfolio_sim(
        series_by_symbol={"AAA/USDT": series},
        symbol_order=("AAA/USDT",),
        config=_cfg(stop_loss_multiple=0.97),
        rank_fn=_rank_fixed(("AAA/USDT",)),
        ma_strategy=MACrossover(MACrossoverParams(20, 50)),
    )
    assert all(item.exit_reason != "stop_loss" for item in result.trades)
    assert result.open_positions


def test_stop_gap_open_fills_at_open_not_stop_price() -> None:
    buy_open = 100.0
    max_sell = max_sell_fill_for_stop(buy_fill=buy_open, commission_rate=0.0)
    gap = max_sell * 0.5
    series = _series(
        "AAA/USDT",
        [(10.0, 10.0), (buy_open, buy_open), (gap, gap), (gap, gap)],
    )
    result = run_portfolio_sim(
        series_by_symbol={"AAA/USDT": series},
        symbol_order=("AAA/USDT",),
        config=_cfg(stop_loss_multiple=0.97),
        rank_fn=_rank_fixed(("AAA/USDT",)),
        ma_strategy=MACrossover(MACrossoverParams(20, 50)),
    )
    sl = [item for item in result.trades if item.exit_reason == "stop_loss"]
    assert sl
    assert sl[0].sell_fill == pytest.approx(gap)
    assert sl[0].sell_fill != pytest.approx(max_sell)


def test_ma_and_stop_same_open_single_exit() -> None:
    series = _series(
        "AAA/USDT",
        [(10.0, 11.0), (12.0, 13.0), (11.9, 8.0), (1.0, 1.0), (1.0, 1.0)],
    )
    result = run_portfolio_sim(
        series_by_symbol={"AAA/USDT": series},
        symbol_order=("AAA/USDT",),
        config=_cfg(stop_loss_multiple=0.97),
        rank_fn=_rank_fixed(("AAA/USDT",)),
        ma_strategy=MACrossover(MACrossoverParams(2, 3)),
    )
    exits = [item for item in result.trades if item.symbol == "AAA/USDT"]
    assert len(exits) == 1
    assert exits[0].exit_reason == "ma_sell"
    assert exits[0].sell_fill == pytest.approx(1.0)


def test_default_config_still_take_profits() -> None:
    series = _series("AAA/USDT", [(10.0, 10.0), (100.0, 100.0), (200.0, 200.0), (200.0, 200.0)])
    result = run_portfolio_sim(
        series_by_symbol={"AAA/USDT": series},
        symbol_order=("AAA/USDT",),
        config=PortfolioSimConfig(initial_cash=10_000.0, commission_rate=0.0),
        rank_fn=_rank_fixed(("AAA/USDT",)),
        ma_strategy=MACrossover(MACrossoverParams(20, 50)),
    )
    assert result.config.net_profit_multiple == 1.01
    assert result.config.stop_loss_multiple is None
    assert any(item.exit_reason == "take_profit" for item in result.trades)


def test_stop_loss_accounting_identity() -> None:
    series = _series("AAA/USDT", [(10.0, 10.0), (100.0, 100.0), (90.0, 90.0), (90.0, 90.0)])
    result = run_portfolio_sim(
        series_by_symbol={"AAA/USDT": series},
        symbol_order=("AAA/USDT",),
        config=_cfg(stop_loss_multiple=0.97, commission_rate=0.001),
        rank_fn=_rank_fixed(("AAA/USDT",)),
        ma_strategy=MACrossover(MACrossoverParams(20, 50)),
    )
    report = analyze_portfolio(result)
    realized = sum(item.pnl for item in result.trades)
    assert result.final_cash + report.open_mark_value == pytest.approx(result.final_equity)
    assert realized + report.unrealized_pnl == pytest.approx(result.final_equity - 10_000.0)
    first = run_portfolio_sim(
        series_by_symbol={"AAA/USDT": series},
        symbol_order=("AAA/USDT",),
        config=_cfg(stop_loss_multiple=0.97, commission_rate=0.001),
        rank_fn=_rank_fixed(("AAA/USDT",)),
        ma_strategy=MACrossover(MACrossoverParams(20, 50)),
    )
    assert first.trades == result.trades
    assert first.final_equity == result.final_equity


def test_empty_input_rejected() -> None:
    with pytest.raises(ValueError, match="symbol_order"):
        run_portfolio_sim(
            series_by_symbol={"A/USDT": _series("A/USDT", [(1.0, 1.0)])},
            symbol_order=(),
            config=_cfg(),
        )
    with pytest.raises(ValueError, match="series_by_symbol"):
        run_portfolio_sim(
            series_by_symbol={},
            symbol_order=("A/USDT",),
            config=_cfg(),
        )

from collections.abc import Mapping, Sequence

from tradingbot.data.ohlcv.models import Candle
from tradingbot.portfolio_sim.engine import run_portfolio_sim
from tradingbot.portfolio_sim.models import PortfolioSimConfig
from tradingbot.portfolio_sim.ranking import is_current_closed_prefix, rank_closed_prefixes
from tradingbot.strategy.ma_crossover import MACrossover, MACrossoverParams
from tradingbot.strategy.models import CandleSeries

TF = 14_400_000


def _feature_series(
    symbol: str,
    n: int,
    *,
    last_close: float,
    start: int = 0,
) -> CandleSeries:
    candles = []
    for i in range(n):
        close = 10.0 + i * 0.001
        if i == n - 1:
            close = last_close
        ts = (start + i) * TF
        candles.append(
            Candle(
                timestamp=ts,
                open=close,
                high=close,
                low=close,
                close=close,
                volume=1.0,
            )
        )
    return CandleSeries.from_closed(
        symbol=symbol,
        timeframe="4h",
        candles=candles,
        now_ms=candles[-1].timestamp + TF,
    )


def test_current_prefix_accepted_one_behind_rejected() -> None:
    series = _feature_series("AAA/USDT", 50, last_close=12.0)
    last = series.candles[-1].timestamp
    assert is_current_closed_prefix(series, now_ms=last + TF, timeframe="4h")
    assert not is_current_closed_prefix(series, now_ms=last + 2 * TF, timeframe="4h")
    ranked = rank_closed_prefixes(
        {"AAA/USDT": series},
        ("AAA/USDT",),
        now_ms=last + TF,
    )
    assert ranked == ("AAA/USDT",)
    stale = rank_closed_prefixes(
        {"AAA/USDT": series},
        ("AAA/USDT",),
        now_ms=last + 2 * TF,
    )
    assert stale == ()


def _closed_at(series: CandleSeries, now_ms: int) -> CandleSeries:
    closed = [item for item in series.candles if item.timestamp + TF <= now_ms]
    return CandleSeries.from_closed(
        symbol=series.symbol,
        timeframe="4h",
        candles=closed,
        now_ms=now_ms,
    )


def test_stale_last_bar_not_a_candidate() -> None:
    fresh = _feature_series("FRESH/USDT", 52, last_close=11.0)
    stale = _feature_series("STALE/USDT", 50, last_close=20.0)
    now_ms = 51 * TF
    ranked = rank_closed_prefixes(
        {
            "FRESH/USDT": _closed_at(fresh, now_ms),
            "STALE/USDT": _closed_at(stale, now_ms),
        },
        ("STALE/USDT", "FRESH/USDT"),
        now_ms=now_ms,
    )
    assert ranked == ("FRESH/USDT",)


def test_short_or_unstarted_does_not_block_current() -> None:
    fresh = _feature_series("FRESH/USDT", 50, last_close=11.0)
    short = _feature_series("SHORT/USDT", 3, last_close=30.0)
    ranked = rank_closed_prefixes(
        {"FRESH/USDT": fresh, "SHORT/USDT": short},
        ("SHORT/USDT", "FRESH/USDT"),
        now_ms=49 * TF + TF,
    )
    assert ranked == ("FRESH/USDT",)
    empty = rank_closed_prefixes({}, ("MISSING/USDT",), now_ms=50 * TF)
    assert empty == ()


def test_equal_score_is_deterministic() -> None:
    a = _feature_series("A/USDT", 50, last_close=12.0)
    b = _feature_series("B/USDT", 50, last_close=12.0)
    now_ms = 49 * TF + TF
    first = rank_closed_prefixes(
        {"A/USDT": a, "B/USDT": b},
        ("B/USDT", "A/USDT"),
        now_ms=now_ms,
    )
    second = rank_closed_prefixes(
        {"A/USDT": a, "B/USDT": b},
        ("B/USDT", "A/USDT"),
        now_ms=now_ms,
    )
    assert first == second == ("B/USDT", "A/USDT")


def test_future_bar_not_in_prefix_score() -> None:
    base = _feature_series("AAA/USDT", 50, last_close=11.0)
    future = Candle(
        timestamp=50 * TF,
        open=99.0,
        high=99.0,
        low=99.0,
        close=99.0,
        volume=1.0,
    )
    with_future = CandleSeries.from_closed(
        symbol="AAA/USDT",
        timeframe="4h",
        candles=(*base.candles, future),
        now_ms=future.timestamp + TF,
    )
    now_ms = 49 * TF + TF
    closed = [item for item in with_future.candles if item.timestamp + TF <= now_ms]
    prefix = CandleSeries.from_closed(
        symbol="AAA/USDT",
        timeframe="4h",
        candles=closed,
        now_ms=now_ms,
    )
    assert prefix.candles[-1].timestamp == 49 * TF
    assert prefix.candles[-1].close != 99.0
    ranked = rank_closed_prefixes({"AAA/USDT": prefix}, ("AAA/USDT",), now_ms=now_ms)
    assert ranked == ("AAA/USDT",)


def test_default_ranking_engine_excludes_ended_series() -> None:
    fresh = _feature_series("FRESH/USDT", 56, last_close=11.0)
    stale = _feature_series("STALE/USDT", 50, last_close=20.0)
    seen: list[tuple[str, ...]] = []

    def rank_fn(
        prefixes: Mapping[str, CandleSeries], order: Sequence[str]
    ) -> tuple[str, ...]:
        seen.append(tuple(symbol for symbol in order if symbol in prefixes))
        if not prefixes:
            return ()
        last = max(item.candles[-1].timestamp for item in prefixes.values())
        return rank_closed_prefixes(prefixes, order, now_ms=last + TF)

    result = run_portfolio_sim(
        series_by_symbol={"FRESH/USDT": fresh, "STALE/USDT": stale},
        symbol_order=("STALE/USDT", "FRESH/USDT"),
        config=PortfolioSimConfig(
            initial_cash=10_000.0,
            commission_rate=0.0,
            net_profit_multiple=100.0,
        ),
        rank_fn=rank_fn,
        ma_strategy=MACrossover(MACrossoverParams(20, 50)),
    )
    assert result is not None
    assert any("STALE/USDT" not in names and "FRESH/USDT" in names for names in seen)


def test_default_rank_without_mock_is_deterministic() -> None:
    a = _feature_series("A/USDT", 52, last_close=12.0)
    b = _feature_series("B/USDT", 52, last_close=11.0)
    kwargs = dict(
        series_by_symbol={"A/USDT": a, "B/USDT": b},
        symbol_order=("B/USDT", "A/USDT"),
        config=PortfolioSimConfig(
            initial_cash=10_000.0,
            commission_rate=0.0,
            net_profit_multiple=100.0,
        ),
        ma_strategy=MACrossover(MACrossoverParams(20, 50)),
    )
    first = run_portfolio_sim(**kwargs)
    second = run_portfolio_sim(**kwargs)
    assert first.trades == second.trades
    assert first.open_positions == second.open_positions
    assert first.final_equity == second.final_equity

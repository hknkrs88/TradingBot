import pytest

from tradingbot.data.ohlcv.models import Candle
from tradingbot.research.forward import forward_returns
from tradingbot.strategy.models import CandleSeries

TF = 14_400_000


def _series(closes: list[float], *, gap_after: int | None = None) -> CandleSeries:
    candles: list[Candle] = []
    for index, close in enumerate(closes):
        if gap_after is None:
            ts = index * TF
        elif index <= gap_after:
            ts = index * TF
        else:
            ts = (index + 1) * TF
        candles.append(
            Candle(timestamp=ts, open=close, high=close, low=close, close=close, volume=1.0)
        )
    return CandleSeries.from_closed(
        symbol="AAA/USDT",
        timeframe="4h",
        candles=tuple(candles),
        now_ms=candles[-1].timestamp + TF,
    )


def test_forward_return_formula_and_tail_omitted() -> None:
    series = _series([10.0, 11.0, 12.0, 9.0, 18.0])
    result = forward_returns(series, (1, 2))
    assert result[1] == (
        (0, 11.0 / 10.0 - 1.0),
        (TF, 12.0 / 11.0 - 1.0),
        (2 * TF, 9.0 / 12.0 - 1.0),
        (3 * TF, 18.0 / 9.0 - 1.0),
    )
    assert result[2] == (
        (0, 12.0 / 10.0 - 1.0),
        (TF, 9.0 / 11.0 - 1.0),
        (2 * TF, 18.0 / 12.0 - 1.0),
    )
    assert len(result[1]) == 4
    assert series.candles[-1].timestamp not in {ts for ts, _ in result[1]}


def test_forward_return_skips_gap_instead_of_filling() -> None:
    series = _series([10.0, 11.0, 12.0, 13.0], gap_after=1)
    result = forward_returns(series, (1,))
    stamps = [ts for ts, _ in result[1]]
    assert 0 in stamps
    assert TF not in stamps
    assert 3 * TF in stamps


def test_invalid_horizon_raises() -> None:
    with pytest.raises(ValueError, match="horizon"):
        forward_returns(_series([1.0, 2.0]), (0,))

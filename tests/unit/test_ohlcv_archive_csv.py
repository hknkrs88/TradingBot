from datetime import UTC, datetime

import pytest

from tradingbot.data.ohlcv.archive_csv import (
    ArchiveKlineParseError,
    month_period_ms,
    parse_archive_kline_row,
    parse_archive_kline_rows,
)
from tradingbot.data.ohlcv.models import Candle

HOUR = 3_600_000
DEC_2024 = month_period_ms(2024, 12)
JAN_2025 = month_period_ms(2025, 1)
AUG_2026 = month_period_ms(2026, 8)


def _fields(
    open_time: int | str,
    close_time: int | str,
    *,
    open_: str = "96407.99000000",
    high: str = "96672.49000000",
    low: str = "96260.00000000",
    close: str = "96340.60000000",
    volume: str = "532.95388000",
) -> list[str]:
    return [
        str(open_time),
        open_,
        high,
        low,
        close,
        volume,
        str(close_time),
        "51410648.79613400",
        "154693",
        "246.27919000",
        "23756467.10209110",
        "0",
    ]


def _parse(fields: list[str], period: tuple[int, int] = DEC_2024, timeframe: str = "1h") -> Candle:
    return parse_archive_kline_row(
        fields,
        timeframe=timeframe,
        period_start_ms=period[0],
        period_end_exclusive_ms=period[1],
    )


def test_ms_sample_december_2024() -> None:
    open_ms = 1733011200000
    candle = _parse(_fields(open_ms, open_ms + HOUR - 1), DEC_2024)
    assert candle.timestamp == open_ms
    assert candle.timestamp == int(datetime(2024, 12, 1, tzinfo=UTC).timestamp()) * 1000
    assert candle.open == pytest.approx(96407.99)
    assert candle.high == pytest.approx(96672.49)
    assert candle.low == pytest.approx(96260.0)
    assert candle.close == pytest.approx(96340.6)
    assert candle.volume == pytest.approx(532.95388)


def test_us_sample_january_2025() -> None:
    open_us = 1735689600000000
    candle = _parse(
        _fields(
            open_us,
            open_us + HOUR * 1000 - 1,
            open_="93576.00000000",
            high="94509.42000000",
            low="93489.03000000",
            close="94401.14000000",
            volume="755.99010000",
        ),
        JAN_2025,
    )
    assert candle.timestamp == open_us // 1000
    assert candle.timestamp == int(datetime(2025, 1, 1, tzinfo=UTC).timestamp()) * 1000


def test_us_sample_august_2026() -> None:
    open_us = 1785542400000000
    candle = _parse(
        _fields(
            open_us,
            open_us + HOUR * 1000 - 1,
            open_="62887.88000000",
            high="63024.04000000",
            low="62887.87000000",
            close="62938.01000000",
            volume="517.26897000",
        ),
        AUG_2026,
    )
    assert candle.timestamp == 1785542400000
    assert candle.timestamp == int(datetime(2026, 8, 1, tzinfo=UTC).timestamp()) * 1000


def test_timestamp_outside_expected_period() -> None:
    open_ms = 1733011200000
    with pytest.raises(ArchiveKlineParseError, match="outside expected period"):
        _parse(_fields(open_ms, open_ms + HOUR - 1), JAN_2025)


def test_seconds_timestamp_rejected() -> None:
    open_s = 1733011200
    with pytest.raises(ArchiveKlineParseError, match="seconds timestamps are not accepted"):
        _parse(_fields(open_s, open_s + 3600 - 1), DEC_2024)


def test_ambiguous_unit_rejected() -> None:
    open_ms = 1733011200000
    with pytest.raises(
        ArchiveKlineParseError,
        match="not uniquely milliseconds or microseconds",
    ):
        _parse(_fields(open_ms, open_ms + 1), DEC_2024)


def test_open_close_unit_mismatch() -> None:
    open_ms = 1733011200000
    close_us = open_ms * 1000 + HOUR * 1000 - 1
    with pytest.raises(ArchiveKlineParseError, match="units do not match"):
        _parse(_fields(open_ms, close_us), DEC_2024)


def test_eleven_columns_rejected() -> None:
    row = _fields(1733011200000, 1733011200000 + HOUR - 1)[:11]
    with pytest.raises(ArchiveKlineParseError, match="expected 12 columns, got 11"):
        _parse(row)


def test_thirteen_columns_rejected() -> None:
    row = _fields(1733011200000, 1733011200000 + HOUR - 1) + ["extra"]
    with pytest.raises(ArchiveKlineParseError, match="expected 12 columns, got 13"):
        _parse(row)


def test_header_row_rejected() -> None:
    header = [
        "open time",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "close time",
        "quote volume",
        "trades",
        "taker buy base",
        "taker buy quote",
        "ignore",
    ]
    with pytest.raises(ArchiveKlineParseError, match="open time is not a decimal"):
        _parse(header)


def test_corrupt_decimal_rejected() -> None:
    row = _fields(1733011200000, 1733011200000 + HOUR - 1, high="not-a-price")
    with pytest.raises(ArchiveKlineParseError, match="high is not a decimal"):
        _parse(row)


def test_invalid_ohlc_rejected() -> None:
    row = _fields(
        1733011200000,
        1733011200000 + HOUR - 1,
        open_="2.0",
        high="1.0",
        low="0.5",
        close="1.5",
        volume="1",
    )
    with pytest.raises(ArchiveKlineParseError, match="invalid OHLC"):
        _parse(row)


def test_negative_volume_rejected() -> None:
    row = _fields(1733011200000, 1733011200000 + HOUR - 1, volume="-1")
    with pytest.raises(ArchiveKlineParseError, match="negative volume"):
        _parse(row)


def test_unaligned_open_time_rejected() -> None:
    open_ms = 1733011200000 + 60_000
    with pytest.raises(ArchiveKlineParseError, match="not aligned to timeframe"):
        _parse(_fields(open_ms, open_ms + HOUR - 1), DEC_2024)


def test_same_input_is_deterministic() -> None:
    row = _fields(1733011200000, 1733011200000 + HOUR - 1)
    first = _parse(row)
    second = _parse(row)
    assert first == second


def test_does_not_fill_hourly_gaps() -> None:
    first_open = 1733011200000
    third_open = first_open + 2 * HOUR
    candles = parse_archive_kline_rows(
        [
            _fields(first_open, first_open + HOUR - 1),
            _fields(third_open, third_open + HOUR - 1),
        ],
        timeframe="1h",
        period_start_ms=DEC_2024[0],
        period_end_exclusive_ms=DEC_2024[1],
    )
    assert [c.timestamp for c in candles] == [first_open, third_open]
    assert len(candles) == 2

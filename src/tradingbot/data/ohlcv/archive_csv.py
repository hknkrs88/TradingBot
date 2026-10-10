"""Parse Binance Spot archive kline CSV rows into Candle.

No network, ZIP, checksum, disk store, or sync_ohlcv integration.
Timestamps are normalized to milliseconds at this boundary only.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation

from tradingbot.data.ohlcv.models import Candle
from tradingbot.data.ohlcv.timeframes import timeframe_ms

ARCHIVE_KLINE_COLUMNS = 12


class ArchiveKlineParseError(ValueError):
    """A single archive kline row cannot be mapped to a millisecond Candle."""


def month_period_ms(year: int, month: int) -> tuple[int, int]:
    """UTC calendar month as ``[start_ms, end_exclusive_ms)``."""
    if month < 1 or month > 12:
        raise ArchiveKlineParseError(f"invalid month {month}")
    start = datetime(year, month, 1, tzinfo=UTC)
    if month == 12:
        end = datetime(year + 1, 1, 1, tzinfo=UTC)
    else:
        end = datetime(year, month + 1, 1, tzinfo=UTC)
    return int(start.timestamp()) * 1000, int(end.timestamp()) * 1000


def parse_archive_kline_rows(
    rows: Iterable[Sequence[str]],
    *,
    timeframe: str,
    period_start_ms: int,
    period_end_exclusive_ms: int,
) -> list[Candle]:
    """Parse rows in order. Does not insert missing bars or sort."""
    return [
        parse_archive_kline_row(
            row,
            timeframe=timeframe,
            period_start_ms=period_start_ms,
            period_end_exclusive_ms=period_end_exclusive_ms,
        )
        for row in rows
    ]


def parse_archive_kline_row(
    fields: Sequence[str],
    *,
    timeframe: str,
    period_start_ms: int,
    period_end_exclusive_ms: int,
) -> Candle:
    if len(fields) != ARCHIVE_KLINE_COLUMNS:
        raise ArchiveKlineParseError(
            f"expected {ARCHIVE_KLINE_COLUMNS} columns, got {len(fields)}"
        )
    if period_end_exclusive_ms <= period_start_ms:
        raise ArchiveKlineParseError("period_end_exclusive_ms must be after period_start_ms")

    tf_ms = timeframe_ms(timeframe)
    open_raw = _parse_integer_field(fields[0], "open time")
    close_raw = _parse_integer_field(fields[6], "close time")
    open_dec = _parse_decimal_field(fields[1], "open")
    high_dec = _parse_decimal_field(fields[2], "high")
    low_dec = _parse_decimal_field(fields[3], "low")
    close_dec = _parse_decimal_field(fields[4], "close")
    volume_dec = _parse_decimal_field(fields[5], "volume")

    open_ms = _normalize_open_close_ms(
        open_raw,
        close_raw,
        tf_ms=tf_ms,
        period_start_ms=period_start_ms,
        period_end_exclusive_ms=period_end_exclusive_ms,
    )
    _validate_ohlcv(open_dec, high_dec, low_dec, close_dec, volume_dec)

    return Candle(
        timestamp=open_ms,
        open=float(open_dec),
        high=float(high_dec),
        low=float(low_dec),
        close=float(close_dec),
        volume=float(volume_dec),
    )


def _parse_decimal_field(raw: str, name: str) -> Decimal:
    text = raw.strip()
    if not text:
        raise ArchiveKlineParseError(f"{name} is empty")
    if any(ch.isalpha() for ch in text):
        raise ArchiveKlineParseError(f"{name} is not a decimal: {raw!r}")
    try:
        value = Decimal(text)
    except InvalidOperation as exc:
        raise ArchiveKlineParseError(f"{name} is not a decimal: {raw!r}") from exc
    if not value.is_finite():
        raise ArchiveKlineParseError(f"{name} is not a finite decimal: {raw!r}")
    return value


def _parse_integer_field(raw: str, name: str) -> int:
    value = _parse_decimal_field(raw, name)
    if value != value.to_integral_value():
        raise ArchiveKlineParseError(f"{name} is not an integer: {raw!r}")
    return int(value)


def _try_ms_pair(
    open_raw: int,
    close_raw: int,
    *,
    tf_ms: int,
    period_start_ms: int,
    period_end_exclusive_ms: int,
) -> int | None:
    if not (period_start_ms <= open_raw < period_end_exclusive_ms):
        return None
    if open_raw % tf_ms != 0:
        return None
    if close_raw != open_raw + tf_ms - 1:
        return None
    return open_raw


def _try_us_pair(
    open_raw: int,
    close_raw: int,
    *,
    tf_ms: int,
    period_start_ms: int,
    period_end_exclusive_ms: int,
) -> int | None:
    if open_raw % 1000 != 0:
        return None
    open_ms = open_raw // 1000
    if not (period_start_ms <= open_ms < period_end_exclusive_ms):
        return None
    if open_ms % tf_ms != 0:
        return None
    tf_us = tf_ms * 1000
    if close_raw != open_raw + tf_us - 1:
        return None
    return open_ms


def _looks_like_seconds_pair(open_raw: int, close_raw: int, tf_ms: int) -> bool:
    tf_s = tf_ms // 1000
    if tf_s < 1:
        return False
    return close_raw == open_raw + tf_s - 1 and 1_000_000_000 <= open_raw < 100_000_000_000


def _normalize_open_close_ms(
    open_raw: int,
    close_raw: int,
    *,
    tf_ms: int,
    period_start_ms: int,
    period_end_exclusive_ms: int,
) -> int:
    ms_ok = _try_ms_pair(
        open_raw,
        close_raw,
        tf_ms=tf_ms,
        period_start_ms=period_start_ms,
        period_end_exclusive_ms=period_end_exclusive_ms,
    )
    us_ok = _try_us_pair(
        open_raw,
        close_raw,
        tf_ms=tf_ms,
        period_start_ms=period_start_ms,
        period_end_exclusive_ms=period_end_exclusive_ms,
    )
    accepted = [item for item in (ms_ok, us_ok) if item is not None]
    if len(accepted) == 1:
        return accepted[0]
    if len(accepted) > 1:
        raise ArchiveKlineParseError(
            "ambiguous timestamp unit: both milliseconds and microseconds fit"
        )
    if _looks_like_seconds_pair(open_raw, close_raw, tf_ms):
        raise ArchiveKlineParseError(
            "seconds timestamps are not accepted; expected milliseconds or microseconds"
        )
    if _open_close_unit_mismatch(open_raw, close_raw, tf_ms):
        raise ArchiveKlineParseError("open time and close time units do not match")
    in_period_ms = period_start_ms <= open_raw < period_end_exclusive_ms
    in_period_us = (
        open_raw % 1000 == 0
        and period_start_ms <= open_raw // 1000 < period_end_exclusive_ms
    )
    if in_period_ms and open_raw % tf_ms != 0:
        raise ArchiveKlineParseError(
            f"open time {open_raw} is not aligned to timeframe {tf_ms} ms"
        )
    if in_period_us and (open_raw // 1000) % tf_ms != 0:
        raise ArchiveKlineParseError(
            f"open time {open_raw} is not aligned to timeframe {tf_ms} ms"
        )
    if not in_period_ms and not in_period_us:
        raise ArchiveKlineParseError(
            f"open time {open_raw} is outside expected period "
            f"[{period_start_ms}, {period_end_exclusive_ms})"
        )
    raise ArchiveKlineParseError(
        "timestamp unit is not uniquely milliseconds or microseconds"
    )


def _open_close_unit_mismatch(open_raw: int, close_raw: int, tf_ms: int) -> bool:
    """True when each timestamp fits a different documented unit."""
    open_ms_scale = 100_000_000_000 <= open_raw < 100_000_000_000_000
    open_us_scale = open_raw >= 100_000_000_000_000
    close_ms_scale = 100_000_000_000 <= close_raw < 100_000_000_000_000
    close_us_scale = close_raw >= 100_000_000_000_000
    if open_ms_scale and close_us_scale:
        return True
    if open_us_scale and close_ms_scale:
        return True
    tf_us = tf_ms * 1000
    ms_close_for_ms_open = close_raw == open_raw + tf_ms - 1
    us_close_for_ms_open = close_raw == open_raw * 1000 + tf_us - 1
    if open_ms_scale and us_close_for_ms_open and not ms_close_for_ms_open:
        return True
    return False


def _validate_ohlcv(
    open_: Decimal,
    high: Decimal,
    low: Decimal,
    close: Decimal,
    volume: Decimal,
) -> None:
    if volume < 0:
        raise ArchiveKlineParseError("negative volume")
    if open_ < 0 or high < 0 or low < 0 or close < 0:
        raise ArchiveKlineParseError("invalid OHLC: negative price")
    if high < low:
        raise ArchiveKlineParseError("invalid OHLC: high < low")
    if high < open_ or high < close:
        raise ArchiveKlineParseError("invalid OHLC: high below open/close")
    if low > open_ or low > close:
        raise ArchiveKlineParseError("invalid OHLC: low above open/close")

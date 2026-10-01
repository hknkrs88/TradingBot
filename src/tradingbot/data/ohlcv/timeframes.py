from __future__ import annotations

ALLOWED_TIMEFRAMES = frozenset({"1h", "4h"})
TIMEFRAME_MS = {
    "1h": 3_600_000,
    "4h": 14_400_000,
}


class UnsupportedTimeframeError(ValueError):
    pass


def timeframe_ms(timeframe: str) -> int:
    if timeframe not in ALLOWED_TIMEFRAMES:
        raise UnsupportedTimeframeError(
            f"unsupported timeframe {timeframe!r}; allowed={sorted(ALLOWED_TIMEFRAMES)}"
        )
    return TIMEFRAME_MS[timeframe]


def validate_timeframes(timeframes: list[str]) -> tuple[str, ...]:
    if not timeframes:
        raise UnsupportedTimeframeError("timeframes must not be empty")
    ordered: list[str] = []
    for item in timeframes:
        timeframe_ms(item)
        if item not in ordered:
            ordered.append(item)
    return tuple(ordered)

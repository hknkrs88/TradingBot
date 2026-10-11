"""Build a candidate symbol tuple from a 4h sufficiency scan. No ranking or orders."""

from __future__ import annotations

from tradingbot.data.ohlcv.universe_sufficiency import (
    UniverseSufficiencyReport,
    scan_safe_for_selection,
)


class SelectionUnsafeError(ValueError):
    """Scan is not safe for later symbol selection."""


def selection_candidates(report: UniverseSufficiencyReport) -> tuple[str, ...]:
    """Return eligible symbols in report order. Does not mutate the report."""
    if not scan_safe_for_selection(report):
        raise SelectionUnsafeError("unsafe_promote_journal blocks selection")
    return tuple(item.symbol for item in report.eligible)

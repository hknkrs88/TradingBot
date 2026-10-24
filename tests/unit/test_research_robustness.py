from datetime import UTC, datetime

import pytest

from tradingbot.research.robustness import (
    MONTH_MIN_N,
    cross_section_raw_and_demeaned,
    group_by_month,
    monthly_spearman,
    nonoverlapping_phase,
    union_phases,
)
from tradingbot.research.spearman import spearman_corr


def test_nonoverlapping_phases_partition_without_duplicate_timestamps() -> None:
    points = tuple((index * 14_400_000, float(index), float(index + 1)) for index in range(12))
    horizon = 3
    phases = [nonoverlapping_phase(points, horizon, phase) for phase in range(horizon)]
    assert [len(item) for item in phases] == [4, 4, 4]
    assert [item[0][0] for item in phases] == [0, 14_400_000, 28_800_000]
    union = union_phases(points, horizon)
    assert len(union) == 12
    assert len({item[0] for item in union}) == 12
    assert union_phases(points, 1) == points


def test_nonoverlap_is_applied_per_symbol_before_pooling() -> None:
    horizon = 2
    symbol_a = tuple((i * 14_400_000, 1.0, 0.1) for i in range(4))
    symbol_b = tuple((i * 14_400_000, 2.0, -0.1) for i in range(4))
    stacked_phase0: list[tuple[int, float, float]] = []
    for series in (symbol_a, symbol_b):
        stacked_phase0.extend(nonoverlapping_phase(series, horizon, 0))
    assert len(stacked_phase0) == 4
    assert [item[0] for item in stacked_phase0] == [0, 28_800_000, 0, 28_800_000]


def test_invalid_phase_raises() -> None:
    points = ((0, 1.0, 0.1), (1, 2.0, 0.2))
    with pytest.raises(ValueError, match="phase"):
        nonoverlapping_phase(points, 2, 2)


def test_monthly_groups_by_utc_month_and_respects_min_n() -> None:
    april = int(datetime(2026, 4, 20, tzinfo=UTC).timestamp() * 1000)
    may = int(datetime(2026, 5, 2, tzinfo=UTC).timestamp() * 1000)
    points = (
        (april, 1.0, 0.1),
        (april + 14_400_000, 2.0, 0.2),
        (may, 3.0, -0.1),
    )
    grouped = group_by_month(points)
    assert set(grouped) == {"2026-04", "2026-05"}
    assert len(grouped["2026-04"]) == 2
    report = monthly_spearman(points, min_n=3)
    assert report["2026-04"]["reason"] == "below_min_n_3"
    assert report["2026-04"]["rho"] is None
    assert MONTH_MIN_N == 30


def test_per_timestamp_demean_matches_raw_spearman_ranks() -> None:
    by_ts = {
        0: [("A", 1.0, 0.10), ("B", 2.0, 0.20), ("C", 3.0, 0.30)],
        1: [("A", 1.0, -0.05), ("B", 3.0, 0.00), ("C", 2.0, 0.05)],
    }
    for rows in by_ts.values():
        xs = [item[1] for item in rows]
        ys = [item[2] for item in rows]
        mean_y = sum(ys) / 3.0
        ys_d = [value - mean_y for value in ys]
        assert spearman_corr(xs, ys).rho == pytest.approx(spearman_corr(xs, ys_d).rho)


def test_stacked_demeaned_differs_from_raw_when_timestamp_means_differ() -> None:
    by_ts = {
        0: [("A", 1.0, 10.0), ("B", 2.0, 10.2), ("C", 3.0, 10.4)] * 4,
        1: [("A", 1.0, -10.0), ("B", 2.0, -9.8), ("C", 3.0, -9.6)] * 4,
    }
    report = cross_section_raw_and_demeaned(by_ts, min_symbols=10)
    raw = report["stacked_eligible_raw"]["rho"]
    demeaned = report["stacked_eligible_demeaned"]["rho"]
    assert raw is not None
    assert demeaned is not None
    assert demeaned != pytest.approx(raw, abs=1e-9)
    assert report["timestamps_skipped_short"] == 0
    assert report["stacked_eligible_n"] == 24

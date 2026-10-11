from pathlib import Path

import pytest

from tradingbot.data.io import InvalidCacheError, atomic_write_json, sha256_file
from tradingbot.data.ohlcv.archive_promote import (
    JOURNAL_SCHEMA,
    inspect_promote_journal,
    promote_journal_dir,
)
from tradingbot.data.ohlcv.models import Candle
from tradingbot.data.ohlcv.selection_candidates import SelectionUnsafeError
from tradingbot.data.ohlcv.store import dump_candles, processed_path
from tradingbot.data.ohlcv.sufficiency import TIMEFRAME
from tradingbot.data.ohlcv.universe_sufficiency import assess_universe_4h
from tradingbot.selection.rank_features import rank_features_4h
from tradingbot.selection.rank_score import RankScoreError, RankScoreRejection, score_features_4h
from tradingbot.selection.select_top_4h import select_top_4h_candidates
from tradingbot.selection.top_candidates import TopCandidatesReport
from tradingbot.strategy.models import CandleSeries

TF = 14_400_000
BTC = "BTC/USDT"
ETH = "ETH/USDT"
SOL = "SOL/USDT"
XRP = "XRP/USDT"
ADA = "ADA/USDT"
DOT = "DOT/USDT"


def _c(index: int, *, close: float = 1.0, volume: float = 1.0) -> Candle:
    return Candle(
        timestamp=index * TF,
        open=close,
        high=close,
        low=close,
        close=close,
        volume=volume,
    )


def _series(count: int, *, base: float = 50.0) -> list[Candle]:
    return [_c(index, close=base + index) for index in range(count)]


def _universe(root: Path, symbols: list[str]) -> Path:
    path = root / "data" / "processed" / "universe.json"
    atomic_write_json(path, {"universe": [{"binance_symbol": item} for item in symbols]})
    return path


def _dump(root: Path, symbol: str, candles: list[Candle], *, timeframe: str = TIMEFRAME) -> Path:
    path = processed_path(root / "data" / "processed" / "ohlcv", symbol, TIMEFRAME)
    dump_candles(path, candles, symbol=symbol, timeframe=timeframe)
    return path


def _hand_score(symbol: str, candles: list[Candle], *, now_ms: int) -> float:
    series = CandleSeries.from_closed(
        symbol=symbol,
        timeframe=TIMEFRAME,
        candles=tuple(candles),
        now_ms=now_ms,
    )
    return score_features_4h(rank_features_4h(series))


def _plant_series_only_journal(root: Path, symbol: str, series_path: Path) -> None:
    manifest_path = root / "data" / "processed" / "ohlcv" / "manifest_4h.json"
    atomic_write_json(manifest_path, {"schema": "ohlcv_dataset_manifest/v1", "timeframe": "4h"})
    pre_series = sha256_file(series_path)
    pre_manifest = sha256_file(manifest_path)
    backup_series = series_path.read_bytes()
    backup_manifest = manifest_path.read_bytes()
    dump_candles(series_path, _series(51), symbol=symbol, timeframe=TIMEFRAME)
    journal_dir = promote_journal_dir(root, symbol, TIMEFRAME)
    journal_dir.mkdir(parents=True, exist_ok=True)
    (journal_dir / "backup_series.json").write_bytes(backup_series)
    (journal_dir / "backup_manifest.json").write_bytes(backup_manifest)
    atomic_write_json(
        journal_dir / "journal.json",
        {
            "schema": JOURNAL_SCHEMA,
            "state": "test",
            "pre_series_sha256": pre_series,
            "pre_manifest_sha256": pre_manifest,
            "backup_series_sha256": sha256_file(journal_dir / "backup_series.json"),
            "backup_manifest_sha256": sha256_file(journal_dir / "backup_manifest.json"),
            "planned_series_sha256": sha256_file(series_path),
            "planned_manifest_sha256": "ab" * 32,
        },
    )
    assert inspect_promote_journal(root, symbol, TIMEFRAME)["kind"] == "series_only"


def test_six_eligible_returns_exactly_top_five(tmp_path: Path) -> None:
    symbols = [BTC, ETH, SOL, XRP, ADA, DOT]
    bases = (60.0, 50.0, 40.0, 30.0, 20.0, 10.0)
    candles = {symbol: _series(50, base=base) for symbol, base in zip(symbols, bases, strict=True)}
    universe = _universe(tmp_path, symbols)
    for symbol in symbols:
        _dump(tmp_path, symbol, candles[symbol])
    now_ms = 50 * TF
    scored = [_hand_score(symbol, candles[symbol], now_ms=now_ms) for symbol in symbols]
    expected = [symbol for symbol, _ in sorted(zip(symbols, scored, strict=True), key=lambda row: (-row[1], symbols.index(row[0])))]
    result = select_top_4h_candidates(universe_path=universe, root=tmp_path, now_ms=now_ms)
    assert isinstance(result, TopCandidatesReport)
    assert [item.symbol for item in result.selected] == expected[:5]
    assert len(result.selected) == 5
    assert DOT == expected[0]
    assert BTC not in {item.symbol for item in result.selected}


def test_fewer_than_five_keeps_ranked_order(tmp_path: Path) -> None:
    universe = _universe(tmp_path, [ETH, BTC, SOL])
    eth = _series(50, base=50.0)
    btc = _series(50, base=10.0)
    sol = _series(50, base=30.0)
    _dump(tmp_path, ETH, eth)
    _dump(tmp_path, BTC, btc)
    _dump(tmp_path, SOL, sol)
    now_ms = 50 * TF
    result = select_top_4h_candidates(universe_path=universe, root=tmp_path, now_ms=now_ms)
    assert [item.symbol for item in result.selected] == [BTC, SOL, ETH]
    assert len(result.selected) == 3


def test_no_eligible_candidates_returns_empty_selected(tmp_path: Path) -> None:
    universe = _universe(tmp_path, [BTC])
    _dump(tmp_path, BTC, _series(49))
    result = select_top_4h_candidates(universe_path=universe, root=tmp_path, now_ms=50 * TF)
    assert result.selected == ()
    assert result.rejected == ()


def test_scan_journal_raises_selection_unsafe_error(tmp_path: Path) -> None:
    universe = _universe(tmp_path, [BTC, ETH])
    series_path = _dump(tmp_path, BTC, _series(50))
    _dump(tmp_path, ETH, _series(50))
    _plant_series_only_journal(tmp_path, BTC, series_path)
    with pytest.raises(SelectionUnsafeError, match="unsafe_promote_journal"):
        select_top_4h_candidates(universe_path=universe, root=tmp_path, now_ms=51 * TF)


def test_reload_journal_raises_invalid_cache_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    universe = _universe(tmp_path, [BTC, ETH])
    series_path = _dump(tmp_path, BTC, _series(50))
    _dump(tmp_path, ETH, _series(50))

    def assess_then_plant(*, universe_path: Path, root: Path, now_ms: int):
        report = assess_universe_4h(universe_path=universe_path, root=root, now_ms=now_ms)
        _plant_series_only_journal(root, BTC, series_path)
        return report

    monkeypatch.setattr(
        "tradingbot.selection.select_top_4h.assess_universe_4h",
        assess_then_plant,
    )
    with pytest.raises(InvalidCacheError, match="unsafe_promote_journal:"):
        select_top_4h_candidates(universe_path=universe, root=tmp_path, now_ms=50 * TF)


def test_feature_and_score_rejections_are_preserved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    universe = _universe(tmp_path, [ETH, BTC, SOL])
    eth = _series(50, base=50.0)
    gapped = _series(49) + [_c(50, close=99.0)]
    sol = _series(50, base=30.0)
    _dump(tmp_path, ETH, eth)
    _dump(tmp_path, BTC, gapped)
    _dump(tmp_path, SOL, sol)
    now_ms = 51 * TF
    eth_score = _hand_score(ETH, eth, now_ms=now_ms)

    def score_or_reject(features) -> float:
        value = score_features_4h(features)
        if value == eth_score:
            raise RankScoreError("non-finite score")
        return value

    monkeypatch.setattr("tradingbot.selection.rank_score.score_features_4h", score_or_reject)
    result = select_top_4h_candidates(universe_path=universe, root=tmp_path, now_ms=now_ms)
    assert [item.symbol for item in result.selected] == [SOL]
    assert [(item.symbol, item.reason) for item in result.rejected] == [
        (BTC, "gap_in_last_50_4h"),
        (ETH, "non_finite_score"),
    ]
    assert isinstance(result.rejected[1], RankScoreRejection)


def test_open_candle_is_filtered_with_scan_now_ms(tmp_path: Path) -> None:
    universe = _universe(tmp_path, [BTC])
    closed = _series(50)
    _dump(tmp_path, BTC, closed + [_c(50, close=999.0)])
    now_ms = 50 * TF + 1
    result = select_top_4h_candidates(universe_path=universe, root=tmp_path, now_ms=now_ms)
    assert len(result.selected) == 1
    assert result.selected[0].symbol == BTC
    assert result.selected[0].score == _hand_score(BTC, closed, now_ms=now_ms)
    assert result.selected[0].score != _hand_score(
        BTC, closed + [_c(50, close=999.0)], now_ms=51 * TF
    )


def test_unexpected_runtime_error_is_not_swallowed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    universe = _universe(tmp_path, [BTC])
    _dump(tmp_path, BTC, _series(50))

    def boom(*args, **kwargs):
        raise RuntimeError("programming bug")

    monkeypatch.setattr("tradingbot.selection.select_top_4h.rank_scored_candidates", boom)
    with pytest.raises(RuntimeError, match="programming bug"):
        select_top_4h_candidates(universe_path=universe, root=tmp_path, now_ms=50 * TF)


def test_same_inputs_are_repeatable(tmp_path: Path) -> None:
    universe = _universe(tmp_path, [ETH, BTC, SOL])
    _dump(tmp_path, ETH, _series(50, base=50.0))
    _dump(tmp_path, BTC, _series(50, base=10.0))
    _dump(tmp_path, SOL, _series(50, base=30.0))
    kwargs = {"universe_path": universe, "root": tmp_path, "now_ms": 50 * TF}
    first = select_top_4h_candidates(**kwargs)
    second = select_top_4h_candidates(**kwargs)
    assert first == second
    assert [item.symbol for item in first.selected] == [BTC, SOL, ETH]


def test_input_files_are_not_modified(tmp_path: Path) -> None:
    universe = _universe(tmp_path, [BTC, ETH])
    btc_path = _dump(tmp_path, BTC, _series(50))
    eth_path = _dump(tmp_path, ETH, _series(49))
    before = {
        universe: universe.read_bytes(),
        btc_path: btc_path.read_bytes(),
        eth_path: eth_path.read_bytes(),
    }
    result = select_top_4h_candidates(universe_path=universe, root=tmp_path, now_ms=50 * TF)
    assert [item.symbol for item in result.selected] == [BTC]
    for path, payload in before.items():
        assert path.read_bytes() == payload

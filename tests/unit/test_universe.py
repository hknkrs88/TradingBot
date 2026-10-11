from __future__ import annotations

import json
from pathlib import Path

import pytest

from tradingbot.config import load_settings
from tradingbot.data.binance_markets import create_public_binance, skip_reason_for_market
from tradingbot.data.coingecko import load_or_fetch_coingecko, validate_coingecko_payload
from tradingbot.data.io import InvalidCacheError, atomic_write_json
from tradingbot.data.match_policy import load_match_policy
from tradingbot.data.stables import StableDenylist, load_stables
from tradingbot.data.universe import (
    UNIVERSE_LIMIT,
    UniverseError,
    build_universe_payload,
    match_universe,
    select_top_universe,
    write_universe_atomic,
)

STAMP = "2026-10-01T21:00:00+00:00"
FIXTURES = Path(__file__).resolve().parent / "fixtures"
POLICY = load_match_policy(Path("configs/universe_match.toml"))


def _stables() -> StableDenylist:
    return load_stables(Path("configs/stables.toml"))


def _spot(base: str, quote: str = "USDT", **over: object) -> dict:
    row = {
        "symbol": f"{base}/{quote}",
        "base": base,
        "quote": quote,
        "active": True,
        "spot": True,
        "margin": False,
        "future": False,
        "swap": False,
        "option": False,
        "contract": False,
        "type": "spot",
        "status": "TRADING",
    }
    row.update(over)
    return row


def _gecko(cid: str, symbol: str, rank: int, name: str | None = None) -> dict:
    return {
        "id": cid,
        "symbol": symbol,
        "name": name or cid,
        "market_cap_rank": rank,
        "market_cap": 10_000_000_000 - rank,
    }


def test_fixture_files_drive_matcher_stable_and_spot_filters() -> None:
    gecko = json.loads((FIXTURES / "coingecko_markets.json").read_text(encoding="utf-8"))
    markets = json.loads((FIXTURES / "binance_spot_markets.json").read_text(encoding="utf-8"))
    assets, skipped = match_universe(gecko, markets, _stables(), POLICY, generated_at=STAMP)
    by_reason = {item["reason"] for item in skipped}
    assert [a["binance_symbol"] for a in assets] == ["BTC/USDT", "ADA/USDT"]
    assert {a["coingecko_id"] for a in assets} == {"bitcoin", "cardano"}
    assert "stable_coingecko_id" in by_reason or "stable_base" in by_reason
    assert "inactive_market" in by_reason
    assert "duplicate_coingecko_ticker" in by_reason
    assert "no_binance_spot_usdt" in by_reason
    assert "status_not_trading" not in {s["reason"] for s in skipped if s.get("ticker") == "ADA"}


def test_public_binance_client_is_not_testnet() -> None:
    exchange = create_public_binance()
    api = exchange.urls.get("api") or {}
    public = str(api.get("public", ""))
    assert "testnet.binance.vision" not in public
    assert "api.binance.com" in public


def test_paper_toml_has_universe_path_not_coin_list() -> None:
    settings = load_settings(Path("configs/paper.toml"))
    assert settings.universe_path == "data/processed/universe.json"
    text = Path("configs/paper.toml").read_text(encoding="utf-8")
    assert "BTC/USDT" in text
    assert "ETH/USDT" not in text


def test_normal_coingecko_binance_match() -> None:
    assets, skipped = match_universe(
        [_gecko("bitcoin", "btc", 1, "Bitcoin"), _gecko("ethereum", "eth", 2, "Ethereum")],
        [_spot("BTC"), _spot("ETH")],
        _stables(),
        POLICY,
        generated_at=STAMP,
    )
    assert [a["binance_symbol"] for a in assets] == ["BTC/USDT", "ETH/USDT"]
    assert assets[0]["coingecko_id"] == "bitcoin"
    assert assets[0]["generated_at"] == STAMP
    assert skipped == []


def test_stablecoin_filter() -> None:
    assets, skipped = match_universe(
        [_gecko("tether", "usdt", 3), _gecko("usd-coin", "usdc", 4), _gecko("bitcoin", "btc", 1)],
        [_spot("USDT"), _spot("USDC"), _spot("BTC")],
        _stables(),
        POLICY,
        generated_at=STAMP,
    )
    assert [a["binance_symbol"] for a in assets] == ["BTC/USDT"]
    reasons = {s["reason"] for s in skipped}
    assert "stable_base" in reasons or "stable_coingecko_id" in reasons


def test_name_containing_usd_is_not_auto_excluded() -> None:
    assets, skipped = match_universe(
        [_gecko("cardano", "ada", 10, "Cardano USD name decoy")],
        [_spot("ADA")],
        _stables(),
        POLICY,
        generated_at=STAMP,
    )
    assert [a["binance_symbol"] for a in assets] == ["ADA/USDT"]
    assert skipped == []


def test_inactive_market() -> None:
    _, skipped = match_universe(
        [_gecko("bitcoin", "btc", 1)],
        [_spot("BTC", active=False)],
        _stables(),
        POLICY,
        generated_at=STAMP,
    )
    assert skipped[0]["reason"] == "inactive_market"


def test_non_spot_market() -> None:
    _, skipped = match_universe(
        [_gecko("bitcoin", "btc", 1)],
        [_spot("BTC", spot=False, type="swap", swap=True, contract=True)],
        _stables(),
        POLICY,
        generated_at=STAMP,
    )
    assert skipped[0]["reason"] in {"non_spot_market", "futures_or_perpetual"}


def test_non_usdt_market() -> None:
    _, skipped = match_universe(
        [_gecko("bitcoin", "btc", 1)],
        [_spot("BTC", "USDC")],
        _stables(),
        POLICY,
        generated_at=STAMP,
    )
    assert skipped[0]["reason"] == "non_usdt_quote"


def test_status_not_trading() -> None:
    _, skipped = match_universe(
        [_gecko("bitcoin", "btc", 1)],
        [_spot("BTC", status="BREAK")],
        _stables(),
        POLICY,
        generated_at=STAMP,
    )
    assert skipped[0]["reason"] == "status_not_trading"


def test_leveraged_token() -> None:
    assert skip_reason_for_market(_spot("BTCUP")) == "leveraged_token"
    assert skip_reason_for_market(_spot("BTC3L")) == "leveraged_token"
    assert skip_reason_for_market(_spot("SYRUP")) is None
    _, skipped = match_universe(
        [_gecko("btc-up", "btcup", 99)],
        [_spot("BTCUP")],
        _stables(),
        POLICY,
        generated_at=STAMP,
    )
    assert skipped[0]["reason"] == "leveraged_token"


def test_duplicate_coingecko_ticker_is_skipped() -> None:
    assets, skipped = match_universe(
        [_gecko("holo", "hot", 50), _gecko("hydro-protocol", "hot", 80), _gecko("bitcoin", "btc", 1)],
        [_spot("HOT"), _spot("BTC")],
        _stables(),
        POLICY,
        generated_at=STAMP,
    )
    assert [a["binance_symbol"] for a in assets] == ["BTC/USDT"]
    dup = next(s for s in skipped if s["reason"] == "duplicate_coingecko_ticker")
    assert dup["ticker"] == "HOT"
    assert set(dup["ids"]) == {"holo", "hydro-protocol"}


def test_ambiguous_binance_markets_skipped() -> None:
    assets, skipped = match_universe(
        [_gecko("bitcoin", "btc", 1)],
        [_spot("BTC"), dict(_spot("BTC"), symbol="BTC/USDT:USDT")],
        _stables(),
        POLICY,
        generated_at=STAMP,
    )
    assert assets == []
    assert skipped[0]["reason"] == "ambiguous_binance_markets"


def test_does_not_guess_1000_prefixed_pair() -> None:
    assets, skipped = match_universe(
        [_gecko("pepe", "pepe", 20)],
        [_spot("1000PEPE")],
        _stables(),
        POLICY,
        generated_at=STAMP,
    )
    assert assets == []
    assert skipped[0]["reason"] == "no_binance_spot_usdt"


def test_empty_gecko_payload_rejected() -> None:
    with pytest.raises(InvalidCacheError):
        validate_coingecko_payload([])


def test_corrupt_cache_does_not_call_network_fallback_without_cache(tmp_path: Path) -> None:
    cache = tmp_path / "coingecko_markets.json"
    cache.write_text("{not-json", encoding="utf-8")

    def boom() -> list:
        raise AssertionError("network fetch must not run in this test")

    with pytest.raises(RuntimeError, match="no valid cache"):
        load_or_fetch_coingecko(cache, refresh=True, fetch_fn=boom)


def test_failed_build_leaves_existing_universe(tmp_path: Path) -> None:
    gecko = tmp_path / "coingecko_markets.json"
    binance = tmp_path / "binance_spot_markets.json"
    universe = tmp_path / "universe.json"
    atomic_write_json(gecko, [_gecko("bitcoin", "btc", 1)])
    atomic_write_json(binance, [_spot("BTC")])
    write_universe_atomic(
        universe,
        [
            {
                "binance_symbol": "BTC/USDT",
                "base": "BTC",
                "quote": "USDT",
                "coingecko_id": "bitcoin",
                "name": "Bitcoin",
                "market_cap_rank": 1,
                "market_cap": 1,
                "generated_at": STAMP,
            }
        ],
        generated_at=STAMP,
        coingecko_raw=gecko,
        binance_raw=binance,
        skipped=[],
    )
    original = universe.read_text(encoding="utf-8")
    gecko.write_text("{broken", encoding="utf-8")
    with pytest.raises(Exception):
        load_or_fetch_coingecko(gecko, refresh=False, fetch_fn=lambda: (_ for _ in ()).throw(RuntimeError("no")))
    assert universe.read_text(encoding="utf-8") == original
    json.loads(original)


def test_written_universe_has_required_fields(tmp_path: Path) -> None:
    gecko = tmp_path / "coingecko_markets.json"
    binance = tmp_path / "binance_spot_markets.json"
    universe = tmp_path / "universe.json"
    atomic_write_json(gecko, [_gecko("bitcoin", "btc", 1, "Bitcoin")])
    atomic_write_json(binance, [_spot("BTC")])
    assets, skipped = match_universe(
        [_gecko("bitcoin", "btc", 1, "Bitcoin")],
        [_spot("BTC")],
        _stables(),
        POLICY,
        generated_at=STAMP,
    )
    write_universe_atomic(
        universe,
        assets,
        generated_at=STAMP,
        coingecko_raw=gecko,
        binance_raw=binance,
        skipped=skipped,
    )
    payload = json.loads(universe.read_text(encoding="utf-8"))
    assert payload["generated_at"] == STAMP
    assert "coingecko_markets" in payload["source_hashes"]
    assert "binance_spot_markets" in payload["source_hashes"]
    row = payload["universe"][0]
    assert row["binance_symbol"] == "BTC/USDT"
    assert row["base"] == "BTC"
    assert row["quote"] == "USDT"
    assert row["coingecko_id"] == "bitcoin"
    assert row["name"] == "Bitcoin"
    assert row["market_cap_rank"] == 1
    assert row["generated_at"] == STAMP


def test_fetch_fn_not_used_when_cache_valid(tmp_path: Path) -> None:
    cache = tmp_path / "coingecko_markets.json"
    atomic_write_json(cache, [_gecko("bitcoin", "btc", 1)])
    rows = load_or_fetch_coingecko(
        cache,
        refresh=False,
        fetch_fn=lambda: (_ for _ in ()).throw(AssertionError("should use cache")),
    )
    assert rows[0]["id"] == "bitcoin"


def _eligible_rows(count: int, *, rank_offset: int = 0) -> tuple[list[dict], list[dict]]:
    gecko: list[dict] = []
    markets: list[dict] = []
    for index in range(count):
        ticker = f"t{index:03d}"
        gecko.append(_gecko(f"id-{ticker}", ticker, index + 1 + rank_offset, ticker))
        markets.append(_spot(ticker.upper()))
    return gecko, markets


def test_select_top_universe_keeps_200_by_market_cap_rank() -> None:
    gecko, markets = _eligible_rows(220)
    gecko[0]["market_cap_rank"] = 500
    gecko[-1]["market_cap_rank"] = 1
    assets, skipped = match_universe(gecko, markets, _stables(), POLICY, generated_at=STAMP)
    selected = select_top_universe(assets, skipped)
    assert len(selected) == UNIVERSE_LIMIT
    ranks = [item["market_cap_rank"] for item in selected]
    assert ranks == sorted(ranks)
    assert ranks[0] == 1
    assert ranks[-1] == 200
    assert selected[0]["binance_symbol"] == "T219/USDT"


def test_build_universe_payload_requires_200_eligible() -> None:
    gecko, markets = _eligible_rows(199)
    with pytest.raises(UniverseError, match="eligible universe too small: 199 < 200"):
        build_universe_payload(gecko, markets, _stables(), POLICY)


def test_filters_still_exclude_stables_before_limit() -> None:
    gecko, markets = _eligible_rows(199)
    gecko.append(_gecko("tether", "usdt", 3))
    markets.append(_spot("USDT"))
    assets, skipped = match_universe(gecko, markets, _stables(), POLICY, generated_at=STAMP)
    assert all(item["binance_symbol"] != "USDT/USDT" for item in assets)
    assert any(item["reason"] in {"stable_base", "stable_coingecko_id"} for item in skipped)
    with pytest.raises(UniverseError, match="too small"):
        select_top_universe(assets, skipped)


def test_inactive_market_does_not_count_toward_limit() -> None:
    gecko, markets = _eligible_rows(199)
    gecko.append(_gecko("idle-coin", "idle", 2))
    markets.append(_spot("IDLE", active=False))
    assets, skipped = match_universe(gecko, markets, _stables(), POLICY, generated_at=STAMP)
    assert skipped[-1]["reason"] == "inactive_market"
    with pytest.raises(UniverseError, match="too small"):
        select_top_universe(assets, skipped)


def test_rank_ties_are_deterministic() -> None:
    gecko = [
        _gecko("id-b", "tbb", 5),
        _gecko("id-a", "taa", 5),
        _gecko("id-c", "tcc", 4),
    ]
    markets = [_spot("TBB"), _spot("TAA"), _spot("TCC")]
    first, _ = match_universe(gecko, markets, _stables(), POLICY, generated_at=STAMP)
    second, _ = match_universe(list(reversed(gecko)), markets, _stables(), POLICY, generated_at=STAMP)
    assert [item["binance_symbol"] for item in first] == ["TCC/USDT", "TAA/USDT", "TBB/USDT"]
    assert [item["binance_symbol"] for item in first] == [item["binance_symbol"] for item in second]


def test_missing_rank_does_not_fill_the_200() -> None:
    gecko, markets = _eligible_rows(199)
    gecko.append(_gecko("id-none", "tnone", 1))
    gecko[-1]["market_cap_rank"] = None
    markets.append(_spot("TNONE"))
    assets, skipped = match_universe(gecko, markets, _stables(), POLICY, generated_at=STAMP)
    with pytest.raises(UniverseError, match="too small"):
        select_top_universe(assets, skipped)
    assert any(item["reason"] == "missing_market_cap_rank" for item in skipped)


def test_bool_float_and_non_positive_ranks_are_ineligible() -> None:
    gecko, markets = _eligible_rows(200)
    invalid_ranks = [True, False, 0, -1, 1.5, "1"]
    for index, rank in enumerate(invalid_ranks):
        ticker = f"x{index:03d}"
        row = _gecko(f"id-{ticker}", ticker, 1)
        row["market_cap_rank"] = rank
        gecko.append(row)
        markets.append(_spot(ticker.upper()))
    assets, skipped, _generated_at = build_universe_payload(gecko, markets, _stables(), POLICY)
    selected_symbols = {item["binance_symbol"] for item in assets}
    assert len(assets) == UNIVERSE_LIMIT
    assert all(f"X{index:03d}/USDT" not in selected_symbols for index in range(len(invalid_ranks)))
    assert sum(item["reason"] == "missing_market_cap_rank" for item in skipped) == len(invalid_ranks)


def test_short_universe_does_not_publish(tmp_path: Path) -> None:
    gecko_path = tmp_path / "coingecko_markets.json"
    binance_path = tmp_path / "binance_spot_markets.json"
    universe_path = tmp_path / "universe.json"
    gecko, markets = _eligible_rows(50)
    atomic_write_json(gecko_path, gecko)
    atomic_write_json(binance_path, markets)
    universe_path.write_text("keep-me", encoding="utf-8")
    with pytest.raises(UniverseError, match="too small"):
        assets, skipped, generated_at = build_universe_payload(
            gecko, markets, _stables(), POLICY
        )
        write_universe_atomic(
            universe_path,
            assets,
            generated_at=generated_at,
            coingecko_raw=gecko_path,
            binance_raw=binance_path,
            skipped=skipped,
        )
    assert universe_path.read_text(encoding="utf-8") == "keep-me"


def test_short_universe_does_not_create_output_file(tmp_path: Path) -> None:
    gecko_path = tmp_path / "coingecko_markets.json"
    binance_path = tmp_path / "binance_spot_markets.json"
    universe_path = tmp_path / "universe.json"
    gecko, markets = _eligible_rows(50)
    atomic_write_json(gecko_path, gecko)
    atomic_write_json(binance_path, markets)
    assert not universe_path.exists()
    with pytest.raises(UniverseError, match="too small"):
        assets, skipped, generated_at = build_universe_payload(
            gecko, markets, _stables(), POLICY
        )
        write_universe_atomic(
            universe_path,
            assets,
            generated_at=generated_at,
            coingecko_raw=gecko_path,
            binance_raw=binance_path,
            skipped=skipped,
        )
    assert not universe_path.exists()


def test_published_universe_is_exactly_200(tmp_path: Path) -> None:
    gecko, markets = _eligible_rows(210)
    gecko_path = tmp_path / "coingecko_markets.json"
    binance_path = tmp_path / "binance_spot_markets.json"
    universe_path = tmp_path / "universe.json"
    atomic_write_json(gecko_path, gecko)
    atomic_write_json(binance_path, markets)
    assets, skipped, generated_at = build_universe_payload(gecko, markets, _stables(), POLICY)
    write_universe_atomic(
        universe_path,
        assets,
        generated_at=generated_at,
        coingecko_raw=gecko_path,
        binance_raw=binance_path,
        skipped=skipped,
    )
    payload = json.loads(universe_path.read_text(encoding="utf-8"))
    assert payload["counts"]["matched"] == UNIVERSE_LIMIT
    assert len(payload["universe"]) == UNIVERSE_LIMIT
    ranks = [row["market_cap_rank"] for row in payload["universe"]]
    assert ranks == list(range(1, UNIVERSE_LIMIT + 1))

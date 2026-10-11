from __future__ import annotations

import json
from pathlib import Path

import pytest

from tradingbot.config import load_settings
from tradingbot.data.binance_markets import create_public_binance, skip_reason_for_market
from tradingbot.data.coingecko import (
    COINGECKO_PER_PAGE,
    gecko_page_cache_path,
    load_or_fetch_coingecko,
    validate_coingecko_payload,
)
from tradingbot.data.io import InvalidCacheError, atomic_write_json, sha256_file
from tradingbot.data.match_policy import load_match_policy
from tradingbot.data.stables import StableDenylist, load_stables
from tradingbot.data.universe import (
    MAX_GECKO_PAGES,
    UNIVERSE_LIMIT,
    UniverseError,
    build_universe_from_gecko_pages,
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


def _eligible_rows(
    count: int, *, rank_offset: int = 0, ticker_offset: int = 0
) -> tuple[list[dict], list[dict]]:
    gecko: list[dict] = []
    markets: list[dict] = []
    for index in range(count):
        ticker = f"t{index + ticker_offset:03d}"
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


def test_gecko_cache_paths_include_page_and_per_page(tmp_path: Path) -> None:
    page1 = gecko_page_cache_path(tmp_path, page=1, per_page=COINGECKO_PER_PAGE)
    page2 = gecko_page_cache_path(tmp_path, page=2, per_page=COINGECKO_PER_PAGE)
    other_size = gecko_page_cache_path(tmp_path, page=1, per_page=100)
    assert page1.name == "coingecko_markets.json"
    assert page2.name == "coingecko_markets_page2_per250.json"
    assert other_size.name == "coingecko_markets_page1_per100.json"
    assert len({page1, page2, other_size}) == 3


def test_first_page_with_200_eligible_does_not_fetch_second_page(tmp_path: Path) -> None:
    gecko, markets = _eligible_rows(200)
    seen: list[int] = []

    def fetch_page(page: int) -> list[dict]:
        seen.append(page)
        if page != 1:
            raise AssertionError("second page must not be requested")
        return gecko

    assets, skipped, _generated_at, _files = build_universe_from_gecko_pages(
        tmp_path, markets, _stables(), POLICY, fetch_page=fetch_page
    )
    assert seen == [1]
    assert MAX_GECKO_PAGES == 4
    assert len(assets) == UNIVERSE_LIMIT
    assert [item["market_cap_rank"] for item in assets] == list(range(1, UNIVERSE_LIMIT + 1))
    assert skipped == []


def test_second_page_fills_universe_to_200(tmp_path: Path) -> None:
    page1, markets1 = _eligible_rows(150)
    page2, markets2 = _eligible_rows(60, rank_offset=150, ticker_offset=150)
    seen: list[int] = []

    def fetch_page(page: int) -> list[dict]:
        seen.append(page)
        return {1: page1, 2: page2}[page]

    assets, _skipped, _generated_at, _files = build_universe_from_gecko_pages(
        tmp_path, markets1 + markets2, _stables(), POLICY, fetch_page=fetch_page
    )
    assert seen == [1, 2]
    assert len(assets) == UNIVERSE_LIMIT
    assert assets[0]["binance_symbol"] == "T000/USDT"
    assert assets[-1]["binance_symbol"] == "T199/USDT"


def test_four_pages_still_short_raises_and_does_not_write(tmp_path: Path) -> None:
    pages: list[list[dict]] = []
    markets: list[dict] = []
    for page_index in range(MAX_GECKO_PAGES):
        gecko, spots = _eligible_rows(
            40, rank_offset=page_index * 40, ticker_offset=page_index * 40
        )
        pages.append(gecko)
        markets.extend(spots)
    universe = tmp_path / "universe.json"
    universe.write_text("keep-me", encoding="utf-8")
    seen: list[int] = []

    def fetch_page(page: int) -> list[dict]:
        seen.append(page)
        return pages[page - 1]

    with pytest.raises(UniverseError, match="eligible universe too small: 160 < 200"):
        assets, skipped, generated_at, files = build_universe_from_gecko_pages(
            tmp_path, markets, _stables(), POLICY, fetch_page=fetch_page
        )
        write_universe_atomic(
            universe,
            assets,
            generated_at=generated_at,
            coingecko_raw=files[0],
            binance_raw=tmp_path / "missing-binance.json",
            skipped=skipped,
            coingecko_page_files=files,
        )
    assert seen == [1, 2, 3, 4]
    assert universe.read_text(encoding="utf-8") == "keep-me"


def test_empty_later_page_is_fail_closed(tmp_path: Path) -> None:
    page1, markets = _eligible_rows(80)
    seen: list[int] = []

    def fetch_page(page: int) -> list[dict]:
        seen.append(page)
        if page == 1:
            return page1
        return []

    with pytest.raises(UniverseError, match="too small"):
        build_universe_from_gecko_pages(
            tmp_path, markets, _stables(), POLICY, fetch_page=fetch_page
        )
    assert seen == [1, 2]


def test_duplicate_coingecko_id_keeps_best_rank(tmp_path: Path) -> None:
    page1, markets = _eligible_rows(199)
    page1[0]["market_cap_rank"] = 500
    duplicate = _gecko("id-t000", "t000", 1, "t000")
    extra, extra_markets = _eligible_rows(1, rank_offset=199, ticker_offset=199)
    seen: list[int] = []

    def fetch_page(page: int) -> list[dict]:
        seen.append(page)
        if page == 1:
            return page1
        if page == 2:
            return [duplicate, extra[0]]
        return []

    assets, _skipped, _generated_at, _files = build_universe_from_gecko_pages(
        tmp_path, markets + extra_markets, _stables(), POLICY, fetch_page=fetch_page
    )
    assert seen == [1, 2]
    by_id = {item["coingecko_id"]: item for item in assets}
    assert by_id["id-t000"]["market_cap_rank"] == 1
    assert len(assets) == UNIVERSE_LIMIT


def test_duplicate_binance_symbol_keeps_best_rank(tmp_path: Path) -> None:
    unique, markets = _eligible_rows(199, ticker_offset=1, rank_offset=2)
    btc = _gecko("bitcoin", "btc", 5)
    wrapped = _gecko("wrapped-btc", "wbtc", 1)
    colliding = dict(_spot("WBTC"), symbol="BTC/USDT")
    gecko = [btc, wrapped] + unique
    all_markets = [_spot("BTC"), colliding] + markets
    assets, skipped, _generated_at = build_universe_payload(
        gecko, all_markets, _stables(), POLICY
    )
    assert len(assets) == UNIVERSE_LIMIT
    btc_rows = [item for item in assets if item["binance_symbol"] == "BTC/USDT"]
    assert len(btc_rows) == 1
    assert btc_rows[0]["coingecko_id"] == "wrapped-btc"
    assert btc_rows[0]["market_cap_rank"] == 1
    assert any(item["reason"] == "duplicate_binance_symbol" for item in skipped)
    assert any(item.get("id") == "bitcoin" for item in skipped)


def test_ambiguous_ticker_across_pages_skips_entire_ticker(tmp_path: Path) -> None:
    page1, markets1 = _eligible_rows(50)
    page1.append(_gecko("holo", "hot", 80))
    page2, markets2 = _eligible_rows(160, rank_offset=50, ticker_offset=50)
    page2.append(_gecko("hydro-protocol", "hot", 90))
    seen: list[int] = []

    def fetch_page(page: int) -> list[dict]:
        seen.append(page)
        return {1: page1, 2: page2}[page]

    assets, skipped, _generated_at, _files = build_universe_from_gecko_pages(
        tmp_path,
        markets1 + markets2 + [_spot("HOT")],
        _stables(),
        POLICY,
        fetch_page=fetch_page,
    )
    assert seen == [1, 2]
    assert all(item["binance_symbol"] != "HOT/USDT" for item in assets)
    assert any(item["reason"] == "duplicate_coingecko_ticker" for item in skipped)
    assert len(assets) == UNIVERSE_LIMIT


def test_paged_invalid_ranks_do_not_count_toward_200(tmp_path: Path) -> None:
    page1, markets1 = _eligible_rows(198)
    bad = _gecko("id-none", "tnone", 1)
    bad["market_cap_rank"] = 1.5
    page2 = [bad]
    seen: list[int] = []

    def fetch_page(page: int) -> list[dict]:
        seen.append(page)
        if page == 1:
            return page1
        if page == 2:
            return page2
        return []

    with pytest.raises(UniverseError, match="too small: 198 < 200"):
        build_universe_from_gecko_pages(
            tmp_path,
            markets1 + [_spot("TNONE")],
            _stables(),
            POLICY,
            fetch_page=fetch_page,
        )
    assert seen[:2] == [1, 2]


def test_stable_and_leveraged_filters_apply_on_later_pages(tmp_path: Path) -> None:
    page1, markets1 = _eligible_rows(199)
    page2 = [
        _gecko("tether", "usdt", 3),
        _gecko("btc-up", "btcup", 4),
        _gecko("id-t199", "t199", 200),
    ]
    seen: list[int] = []

    def fetch_page(page: int) -> list[dict]:
        seen.append(page)
        return {1: page1, 2: page2}[page]

    assets, skipped, _generated_at, _files = build_universe_from_gecko_pages(
        tmp_path,
        markets1 + [_spot("USDT"), _spot("BTCUP"), _spot("T199")],
        _stables(),
        POLICY,
        fetch_page=fetch_page,
    )
    assert seen == [1, 2]
    symbols = {item["binance_symbol"] for item in assets}
    assert "USDT/USDT" not in symbols
    assert "BTCUP/USDT" not in symbols
    assert "T199/USDT" in symbols
    reasons = {item["reason"] for item in skipped}
    assert "stable_coingecko_id" in reasons or "stable_base" in reasons
    assert "leveraged_token" in reasons
    assert len(assets) == UNIVERSE_LIMIT


def test_later_page_fetch_error_leaves_published_universe_bytes(tmp_path: Path) -> None:
    gecko, markets = _eligible_rows(80)
    gecko_path = tmp_path / "coingecko_markets.json"
    binance_path = tmp_path / "binance_spot_markets.json"
    universe = tmp_path / "universe.json"
    atomic_write_json(gecko_path, gecko)
    atomic_write_json(binance_path, markets)
    seed = [
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
    ]
    write_universe_atomic(
        universe,
        seed,
        generated_at=STAMP,
        coingecko_raw=gecko_path,
        binance_raw=binance_path,
        skipped=[],
    )
    original = universe.read_bytes()

    def fetch_page(page: int) -> list[dict]:
        if page == 1:
            return gecko
        raise RuntimeError("page 2 failed")

    try:
        assets, skipped, generated_at, files = build_universe_from_gecko_pages(
            tmp_path, markets, _stables(), POLICY, refresh=True, fetch_page=fetch_page
        )
        write_universe_atomic(
            universe,
            assets,
            generated_at=generated_at,
            coingecko_raw=files[0],
            binance_raw=binance_path,
            skipped=skipped,
            coingecko_page_files=files,
        )
    except Exception:
        pass
    else:
        raise AssertionError("page 2 failure must prevent publish")
    assert universe.read_bytes() == original


def test_legacy_page1_cache_is_not_used_for_page2(tmp_path: Path) -> None:
    page1, markets = _eligible_rows(200)
    atomic_write_json(tmp_path / "coingecko_markets.json", page1)
    seen: list[int] = []

    def fetch_page(page: int) -> list[dict]:
        seen.append(page)
        raise AssertionError(f"network must not run for cached page {page}")

    assets, _skipped, _generated_at, _files = build_universe_from_gecko_pages(
        tmp_path, markets, _stables(), POLICY, fetch_page=fetch_page
    )
    assert seen == []
    assert len(assets) == UNIVERSE_LIMIT

    page2_path = gecko_page_cache_path(tmp_path, page=2, per_page=COINGECKO_PER_PAGE)
    assert page2_path != tmp_path / "coingecko_markets.json"
    seen.clear()

    def fetch_only_page2(page: int) -> list[dict]:
        seen.append(page)
        if page == 1:
            raise AssertionError("page 1 must use legacy cache")
        if page == 2:
            return _eligible_rows(1, ticker_offset=500, rank_offset=500)[0]
        return []

    short, short_markets = _eligible_rows(50)
    atomic_write_json(tmp_path / "coingecko_markets.json", short)
    with pytest.raises(UniverseError):
        build_universe_from_gecko_pages(
            tmp_path,
            markets + short_markets,
            _stables(),
            POLICY,
            fetch_page=fetch_only_page2,
        )
    assert 1 not in seen
    assert seen[0] == 2


def test_paged_rank_order_is_deterministic(tmp_path: Path) -> None:
    page1 = [
        _gecko("id-b", "tbb", 5),
        _gecko("id-a", "taa", 5),
        _gecko("id-c", "tcc", 4),
    ]
    extra, extra_markets = _eligible_rows(197, ticker_offset=10, rank_offset=10)
    markets = [_spot("TBB"), _spot("TAA"), _spot("TCC")] + extra_markets

    def fetch_page(page: int) -> list[dict]:
        if page == 1:
            return page1 + extra
        return []

    first, _skipped, _generated_at, _files = build_universe_from_gecko_pages(
        tmp_path, markets, _stables(), POLICY, fetch_page=fetch_page
    )
    tmp2 = tmp_path / "other"
    tmp2.mkdir()

    def fetch_reversed(page: int) -> list[dict]:
        if page == 1:
            return list(reversed(page1)) + extra
        return []

    second, _skipped2, _generated_at2, _files2 = build_universe_from_gecko_pages(
        tmp2, markets, _stables(), POLICY, fetch_page=fetch_reversed
    )
    assert [item["binance_symbol"] for item in first[:3]] == [
        "TCC/USDT",
        "TAA/USDT",
        "TBB/USDT",
    ]
    assert [item["binance_symbol"] for item in first] == [item["binance_symbol"] for item in second]


@pytest.mark.parametrize(
    ("page1_rank", "page2_rank", "winner_id", "loser_id"),
    (
        (5, 1, "wrapped-btc", "bitcoin"),
        (1, 5, "bitcoin", "wrapped-btc"),
    ),
)
def test_duplicate_binance_symbol_across_pages_keeps_best_rank(
    tmp_path: Path,
    page1_rank: int,
    page2_rank: int,
    winner_id: str,
    loser_id: str,
) -> None:
    unique1, markets1 = _eligible_rows(150)
    unique2, markets2 = _eligible_rows(49, rank_offset=200, ticker_offset=200)
    colliding = dict(_spot("WBTC"), symbol="BTC/USDT")
    page1 = unique1 + [_gecko("bitcoin", "btc", page1_rank)]
    page2 = unique2 + [_gecko("wrapped-btc", "wbtc", page2_rank)]
    seen: list[int] = []

    def fetch_page(page: int) -> list[dict]:
        seen.append(page)
        return {1: page1, 2: page2}[page]

    assets, skipped, _generated_at, _files = build_universe_from_gecko_pages(
        tmp_path,
        markets1 + markets2 + [_spot("BTC"), colliding],
        _stables(),
        POLICY,
        fetch_page=fetch_page,
    )
    assert seen == [1, 2]
    btc_rows = [item for item in assets if item["binance_symbol"] == "BTC/USDT"]
    assert len(btc_rows) == 1
    assert btc_rows[0]["coingecko_id"] == winner_id
    assert btc_rows[0]["market_cap_rank"] == min(page1_rank, page2_rank)
    dupes = [item for item in skipped if item["reason"] == "duplicate_binance_symbol"]
    assert any(item.get("id") == loser_id for item in dupes)
    assert any(item.get("binance_symbol") == "BTC/USDT" for item in dupes)
    assert len(assets) == UNIVERSE_LIMIT


def test_source_hashes_include_each_fetched_gecko_page(tmp_path: Path) -> None:
    page1, markets1 = _eligible_rows(150)
    page2_a, markets2 = _eligible_rows(60, rank_offset=150, ticker_offset=150)
    page2_b = [dict(row) for row in page2_a]
    page2_b[0] = dict(page2_b[0], name="page-2-changed")
    gecko_dir = tmp_path / "raw_a"
    gecko_dir.mkdir()
    binance_path = tmp_path / "binance_spot_markets.json"
    atomic_write_json(binance_path, markets1 + markets2)
    page1_path = gecko_page_cache_path(gecko_dir, page=1, per_page=COINGECKO_PER_PAGE)
    page2_path = gecko_page_cache_path(gecko_dir, page=2, per_page=COINGECKO_PER_PAGE)

    def fetch_a(page: int) -> list[dict]:
        return {1: page1, 2: page2_a}[page]

    assets, skipped, generated_at, files = build_universe_from_gecko_pages(
        gecko_dir, markets1 + markets2, _stables(), POLICY, fetch_page=fetch_a
    )
    universe = tmp_path / "universe.json"
    write_universe_atomic(
        universe,
        assets,
        generated_at=generated_at,
        coingecko_raw=page1_path,
        binance_raw=binance_path,
        skipped=skipped,
        coingecko_page_files=files,
    )
    payload = json.loads(universe.read_text(encoding="utf-8"))
    hashes = payload["source_hashes"]
    assert hashes["coingecko_markets"] == sha256_file(page1_path)
    assert hashes["coingecko_market_pages"][page1_path.name] == sha256_file(page1_path)
    assert hashes["coingecko_market_pages"][page2_path.name] == sha256_file(page2_path)
    assert set(hashes["coingecko_market_pages"]) == {page1_path.name, page2_path.name}
    first_page1 = hashes["coingecko_markets"]
    first_page2 = hashes["coingecko_market_pages"][page2_path.name]

    def fetch_b(page: int) -> list[dict]:
        return {1: page1, 2: page2_b}[page]

    assets_b, skipped_b, generated_at_b, files_b = build_universe_from_gecko_pages(
        gecko_dir,
        markets1 + markets2,
        _stables(),
        POLICY,
        refresh=True,
        fetch_page=fetch_b,
    )
    write_universe_atomic(
        universe,
        assets_b,
        generated_at=generated_at_b,
        coingecko_raw=page1_path,
        binance_raw=binance_path,
        skipped=skipped_b,
        coingecko_page_files=files_b,
    )
    payload_b = json.loads(universe.read_text(encoding="utf-8"))
    hashes_b = payload_b["source_hashes"]
    assert hashes_b["coingecko_markets"] == first_page1
    assert hashes_b["coingecko_markets"] == sha256_file(page1_path)
    assert hashes_b["coingecko_market_pages"][page1_path.name] == first_page1
    assert hashes_b["coingecko_market_pages"][page2_path.name] != first_page2
    assert hashes_b["coingecko_market_pages"][page2_path.name] == sha256_file(page2_path)

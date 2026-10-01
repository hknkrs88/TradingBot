from pathlib import Path
import json

from tradingbot.data.match_policy import load_match_policy
from tradingbot.data.stables import load_stables
from tradingbot.data.universe import match_universe

STAMP = "2026-10-01T21:00:00+00:00"
FIXTURES = Path(__file__).resolve().parent / "fixtures"
POLICY = load_match_policy(FIXTURES / "universe_match.toml")
STABLES = load_stables(Path("configs/stables.toml"))
MARKETS = json.loads((FIXTURES / "ambiguity_markets.json").read_text(encoding="utf-8"))


def _load_gecko(name: str) -> list[dict]:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def test_wrong_coingecko_id_on_overridden_ticker_is_skipped() -> None:
    assets, skipped = match_universe(
        _load_gecko("ambiguity_wrong_id_gecko.json"),
        MARKETS,
        STABLES,
        POLICY,
        generated_at=STAMP,
    )
    assert assets == []
    assert skipped[0]["reason"] == "override_id_mismatch"
    assert skipped[0]["id"] == "artificial-inu-3"
    assert skipped[0]["expected_id"] == "sleepless-ai"


def test_explicit_override_matches() -> None:
    assets, skipped = match_universe(
        _load_gecko("ambiguity_override_ok_gecko.json"),
        MARKETS,
        STABLES,
        POLICY,
        generated_at=STAMP,
    )
    assert [a["binance_symbol"] for a in assets] == ["AI/USDT"]
    assert assets[0]["coingecko_id"] == "sleepless-ai"
    assert skipped == []


def test_unique_ticker_matches_without_override() -> None:
    gecko = [row for row in _load_gecko("ambiguity_filters_gecko.json") if row["id"] == "bitcoin"]
    assets, skipped = match_universe(gecko, MARKETS, STABLES, POLICY, generated_at=STAMP)
    assert [a["binance_symbol"] for a in assets] == ["BTC/USDT"]
    assert skipped == []


def test_stablecoin_skipped_from_fixture() -> None:
    gecko = [row for row in _load_gecko("ambiguity_filters_gecko.json") if row["id"] == "tether"]
    assets, skipped = match_universe(gecko, MARKETS, STABLES, POLICY, generated_at=STAMP)
    assert assets == []
    assert skipped[0]["reason"] in {"stable_coingecko_id", "stable_base"}


def test_non_usdt_skipped_from_fixture() -> None:
    gecko = [row for row in _load_gecko("ambiguity_filters_gecko.json") if row["id"] == "pepe"]
    assets, skipped = match_universe(gecko, MARKETS, STABLES, POLICY, generated_at=STAMP)
    assert assets == []
    assert skipped[0]["reason"] == "non_usdt_quote"


def test_futures_and_leveraged_skipped_from_fixture() -> None:
    gecko = [
        row
        for row in _load_gecko("ambiguity_filters_gecko.json")
        if row["id"] in {"dogecoin", "btc-up"}
    ]
    assets, skipped = match_universe(gecko, MARKETS, STABLES, POLICY, generated_at=STAMP)
    assert assets == []
    reasons = {item["reason"] for item in skipped}
    assert "leveraged_token" in reasons
    assert reasons & {"futures_or_perpetual", "non_spot_market"}

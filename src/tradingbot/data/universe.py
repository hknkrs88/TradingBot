from __future__ import annotations

import logging
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from tradingbot.data.binance_markets import skip_reason_for_market
from tradingbot.data.io import atomic_write_json, sha256_file
from tradingbot.data.match_policy import MatchPolicy
from tradingbot.data.stables import StableDenylist

log = logging.getLogger("tradingbot.data.universe")

UNIVERSE_LIMIT = 200


class UniverseError(ValueError):
    """Matched universe cannot be published."""


def _valid_market_cap_rank(rank: Any) -> bool:
    return type(rank) is int and rank > 0


def _rank_sort_key(item: dict[str, Any]) -> tuple[int, int, str]:
    rank = item.get("market_cap_rank")
    symbol = str(item.get("binance_symbol") or "")
    if not _valid_market_cap_rank(rank):
        return (1, 0, symbol)
    return (0, rank, symbol)


def _index_markets_by_base(markets: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    index: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for market in markets:
        base = str(market.get("base") or "").upper()
        if base:
            index[base].append(market)
    return index


def _skip(
    skipped: list[dict[str, Any]],
    *,
    reason: str,
    ticker: str,
    gecko_id: str = "",
    extra: dict[str, Any] | None = None,
) -> None:
    event = {"reason": reason, "source": "match", "ticker": ticker, "id": gecko_id}
    if extra:
        event.update(extra)
    skipped.append(event)
    log.info("skip_match reason=%s ticker=%s id=%s", reason, ticker, gecko_id)


def match_universe(
    gecko_rows: list[dict[str, Any]],
    markets: list[dict[str, Any]],
    stables: StableDenylist,
    policy: MatchPolicy,
    *,
    generated_at: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    skipped: list[dict[str, Any]] = []
    by_base = _index_markets_by_base(markets)

    by_ticker: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in gecko_rows:
        ticker = str(row.get("symbol") or "").upper()
        gecko_id = str(row.get("id") or "")
        if not ticker or not gecko_id:
            skipped.append(
                {
                    "reason": "missing_fields",
                    "source": "coingecko",
                    "id": gecko_id,
                    "ticker": ticker,
                }
            )
            continue
        by_ticker[ticker].append(row)

    matched: list[dict[str, Any]] = []
    for ticker, rows in by_ticker.items():
        ids = sorted({str(r.get("id")) for r in rows})
        if len(ids) > 1:
            event = {
                "reason": "duplicate_coingecko_ticker",
                "source": "coingecko",
                "ticker": ticker,
                "ids": ids,
            }
            skipped.append(event)
            log.info("skip_match reason=%s ticker=%s ids=%s", event["reason"], ticker, ids)
            continue

        row = rows[0]
        gecko_id = str(row["id"])
        if stables.blocks_id(gecko_id) or stables.blocks_base(ticker):
            skipped.append(
                {
                    "reason": "stable_coingecko_id" if stables.blocks_id(gecko_id) else "stable_base",
                    "source": "coingecko",
                    "ticker": ticker,
                    "id": gecko_id,
                }
            )
            continue

        override = policy.override_for(ticker)
        if override is not None and gecko_id.lower() != override.coingecko_id:
            _skip(
                skipped,
                reason="override_id_mismatch",
                ticker=ticker,
                gecko_id=gecko_id,
                extra={"expected_id": override.coingecko_id},
            )
            continue
        if override is None and policy.requires_override(ticker):
            reason = (
                "short_ticker_requires_override"
                if len(ticker) < policy.min_auto_ticker_length
                else "ambiguous_ticker_requires_override"
            )
            _skip(skipped, reason=reason, ticker=ticker, gecko_id=gecko_id)
            continue

        raw_candidates = by_base.get(ticker, [])
        eligible: list[dict[str, Any]] = []
        market_reasons: list[str] = []
        for market in raw_candidates:
            reason = skip_reason_for_market(market)
            if reason is None and stables.blocks_base(str(market.get("base") or "")):
                reason = "stable_base"
            if reason:
                market_reasons.append(reason)
                continue
            eligible.append(market)

        if len(eligible) > 1:
            symbols = [c.get("symbol") for c in eligible]
            _skip(
                skipped,
                reason="ambiguous_binance_markets",
                ticker=ticker,
                gecko_id=gecko_id,
                extra={"symbols": symbols},
            )
            continue

        if len(eligible) == 1:
            market = eligible[0]
            if override is not None and market["symbol"] != override.binance_symbol:
                _skip(
                    skipped,
                    reason="override_symbol_mismatch",
                    ticker=ticker,
                    gecko_id=gecko_id,
                    extra={
                        "expected_symbol": override.binance_symbol,
                        "actual_symbol": market["symbol"],
                    },
                )
                continue
            matched.append(
                {
                    "binance_symbol": market["symbol"],
                    "base": market["base"],
                    "quote": market["quote"],
                    "coingecko_id": gecko_id,
                    "name": row.get("name"),
                    "market_cap_rank": row.get("market_cap_rank"),
                    "market_cap": row.get("market_cap"),
                    "generated_at": generated_at,
                }
            )
            continue

        reason = market_reasons[0] if market_reasons else "no_binance_spot_usdt"
        _skip(skipped, reason=reason, ticker=ticker, gecko_id=gecko_id)

    matched.sort(key=_rank_sort_key)
    return matched, skipped


def select_top_universe(
    assets: list[dict[str, Any]],
    skipped: list[dict[str, Any]],
    *,
    limit: int = UNIVERSE_LIMIT,
) -> list[dict[str, Any]]:
    """Keep the lowest valid market_cap_rank rows, size exactly ``limit``.

    Missing or non-positive ranks are not eligible. Fewer than ``limit``
    eligible rows is an error; a short list is not returned.
    """
    eligible: list[dict[str, Any]] = []
    for item in assets:
        if not _valid_market_cap_rank(item.get("market_cap_rank")):
            skipped.append(
                {
                    "reason": "missing_market_cap_rank",
                    "source": "rank",
                    "ticker": str(item.get("base") or ""),
                    "id": str(item.get("coingecko_id") or ""),
                }
            )
            continue
        eligible.append(item)
    eligible.sort(key=_rank_sort_key)
    if len(eligible) < limit:
        raise UniverseError(
            f"eligible universe too small: {len(eligible)} < {limit}"
        )
    return eligible[:limit]


def write_universe_atomic(
    path: Path,
    assets: list[dict[str, Any]],
    *,
    generated_at: str,
    coingecko_raw: Path,
    binance_raw: Path,
    skipped: list[dict[str, Any]],
) -> None:
    payload = {
        "generated_at": generated_at,
        "source_hashes": {
            "coingecko_markets": sha256_file(coingecko_raw),
            "binance_spot_markets": sha256_file(binance_raw),
        },
        "counts": {
            "matched": len(assets),
            "skipped": len(skipped),
        },
        "universe": assets,
    }
    atomic_write_json(path, payload)


def build_universe_payload(
    gecko_rows: list[dict[str, Any]],
    markets: list[dict[str, Any]],
    stables: StableDenylist,
    policy: MatchPolicy,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], str]:
    generated_at = datetime.now(UTC).replace(microsecond=0).isoformat()
    assets, skipped = match_universe(
        gecko_rows, markets, stables, policy, generated_at=generated_at
    )
    assets = select_top_universe(assets, skipped)
    return assets, skipped, generated_at

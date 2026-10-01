from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Callable

import httpx

from tradingbot.data.io import InvalidCacheError, atomic_write_json, load_json

log = logging.getLogger("tradingbot.data.coingecko")

COINGECKO_MARKETS_URL = "https://api.coingecko.com/api/v3/coins/markets"
COINGECKO_PARAMS = {
    "vs_currency": "usd",
    "order": "market_cap_desc",
    "per_page": 250,
    "page": 1,
    "sparkline": "false",
}


def validate_coingecko_payload(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, list) or not payload:
        raise InvalidCacheError("coingecko cache must be a non-empty list")
    rows: list[dict[str, Any]] = []
    for item in payload:
        if not isinstance(item, dict) or "id" not in item or "symbol" not in item:
            raise InvalidCacheError("coingecko row missing id/symbol")
        rows.append(item)
    return rows


def load_coingecko_cache(path: Path) -> list[dict[str, Any]]:
    return validate_coingecko_payload(load_json(path))


def fetch_coingecko_markets(
    client: httpx.Client | None = None,
) -> list[dict[str, Any]]:
    headers = {"Accept": "application/json", "User-Agent": "tradingbot-universe/0.1"}
    own_client = client is None
    http = client or httpx.Client(timeout=30.0, headers=headers)
    try:
        response = http.get(COINGECKO_MARKETS_URL, params=COINGECKO_PARAMS)
        response.raise_for_status()
        return validate_coingecko_payload(response.json())
    finally:
        if own_client:
            http.close()


def load_or_fetch_coingecko(
    cache_path: Path,
    *,
    refresh: bool = False,
    fetch_fn: Callable[[], list[dict[str, Any]]] | None = None,
) -> list[dict[str, Any]]:
    fetch = fetch_fn or fetch_coingecko_markets
    cached: list[dict[str, Any]] | None = None
    try:
        cached = load_coingecko_cache(cache_path)
    except InvalidCacheError:
        cached = None

    if cached is not None and not refresh:
        log.info("using coingecko cache path=%s rows=%s", cache_path, len(cached))
        return cached

    try:
        rows = fetch()
        atomic_write_json(cache_path, rows)
        log.info("wrote coingecko cache path=%s rows=%s", cache_path, len(rows))
        return rows
    except Exception as exc:
        if cached is not None:
            log.warning("coingecko fetch failed; using cache error=%s", exc)
            return cached
        raise RuntimeError("coingecko fetch failed and no valid cache exists") from exc

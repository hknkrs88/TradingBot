from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Callable

import httpx

from tradingbot.data.io import InvalidCacheError, atomic_write_json, load_json

log = logging.getLogger("tradingbot.data.coingecko")

COINGECKO_MARKETS_URL = "https://api.coingecko.com/api/v3/coins/markets"
COINGECKO_PER_PAGE = 250
COINGECKO_PARAMS = {
    "vs_currency": "usd",
    "order": "market_cap_desc",
    "per_page": COINGECKO_PER_PAGE,
    "page": 1,
    "sparkline": "false",
}
_LEGACY_CACHE_NAME = "coingecko_markets.json"


def gecko_page_cache_path(raw_dir: Path, *, page: int, per_page: int) -> Path:
    """Return the cache file for one CoinGecko markets page.

    Page 1 with the default ``per_page`` keeps the legacy filename so an
    existing single-page cache cannot be reused for any other page.
    """
    if page == 1 and per_page == COINGECKO_PER_PAGE:
        return Path(raw_dir) / _LEGACY_CACHE_NAME
    return Path(raw_dir) / f"coingecko_markets_page{page}_per{per_page}.json"


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
    *,
    page: int = 1,
    per_page: int = COINGECKO_PER_PAGE,
) -> list[dict[str, Any]]:
    headers = {"Accept": "application/json", "User-Agent": "tradingbot-universe/0.1"}
    params = {**COINGECKO_PARAMS, "page": page, "per_page": per_page}
    own_client = client is None
    http = client or httpx.Client(timeout=30.0, headers=headers)
    try:
        response = http.get(COINGECKO_MARKETS_URL, params=params)
        response.raise_for_status()
        payload = response.json()
        if isinstance(payload, list) and not payload:
            return []
        return validate_coingecko_payload(payload)
    finally:
        if own_client:
            http.close()


def load_or_fetch_coingecko(
    cache_path: Path,
    *,
    refresh: bool = False,
    fetch_fn: Callable[[], list[dict[str, Any]]] | None = None,
    page: int = 1,
    per_page: int = COINGECKO_PER_PAGE,
) -> list[dict[str, Any]]:
    fetch = fetch_fn or (lambda: fetch_coingecko_markets(page=page, per_page=per_page))
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
        if not rows:
            return []
        rows = validate_coingecko_payload(rows)
        atomic_write_json(cache_path, rows)
        log.info("wrote coingecko cache path=%s rows=%s", cache_path, len(rows))
        return rows
    except Exception as exc:
        if cached is not None:
            log.warning("coingecko fetch failed; using cache error=%s", exc)
            return cached
        raise RuntimeError("coingecko fetch failed and no valid cache exists") from exc

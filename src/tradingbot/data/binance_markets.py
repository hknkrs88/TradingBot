from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any, Callable

import ccxt

from tradingbot.data.io import InvalidCacheError, atomic_write_json, load_json

log = logging.getLogger("tradingbot.data.binance_markets")

_LEVERAGED_MULT = re.compile(r"^[A-Z0-9]+(3L|3S|5L|5S|BULL|BEAR)$")
# *UP/*DOWN only when parent is a known BLVT underlying (not SYRUP/JUP).
_BLVT_PARENTS = frozenset(
    {
        "BTC",
        "ETH",
        "BNB",
        "ADA",
        "DOT",
        "XRP",
        "LINK",
        "UNI",
        "SOL",
        "MATIC",
        "FIL",
        "AAVE",
        "SUSHI",
        "BCH",
        "LTC",
        "TRX",
        "EOS",
        "XTZ",
        "ATOM",
    }
)


def _is_leveraged_token(base: str) -> bool:
    if _LEVERAGED_MULT.match(base):
        return True
    if base.endswith("DOWN") and base[:-4] in _BLVT_PARENTS:
        return True
    if base.endswith("UP") and base[:-2] in _BLVT_PARENTS:
        return True
    return False


def create_public_binance() -> ccxt.binance:
    """Unauthenticated live Spot metadata client. Not the paper trading client."""
    return ccxt.binance(
        {
            "enableRateLimit": True,
            "options": {
                "defaultType": "spot",
                "adjustForTimeDifference": True,
            },
        }
    )


def normalize_market(market: dict[str, Any]) -> dict[str, Any]:
    info = market.get("info") if isinstance(market.get("info"), dict) else {}
    status = info.get("status") or market.get("status")
    return {
        "symbol": market.get("symbol"),
        "base": market.get("base"),
        "quote": market.get("quote"),
        "active": market.get("active"),
        "spot": bool(market.get("spot")),
        "margin": bool(market.get("margin")),
        "future": bool(market.get("future")),
        "swap": bool(market.get("swap")),
        "option": bool(market.get("option")),
        "contract": bool(market.get("contract")),
        "type": market.get("type"),
        "status": status,
    }


def skip_reason_for_market(market: dict[str, Any]) -> str | None:
    base = str(market.get("base") or "")
    quote = str(market.get("quote") or "")
    symbol = str(market.get("symbol") or "")
    status = str(market.get("status") or "").upper()
    market_type = str(market.get("type") or "").lower()

    if market.get("future") or market.get("swap") or market.get("option") or market.get("contract"):
        return "futures_or_perpetual"
    if market_type and market_type != "spot":
        return "non_spot_market"
    if market.get("spot") is False:
        return "non_spot_market"
    if quote != "USDT":
        return "non_usdt_quote"
    if market.get("active") is False:
        return "inactive_market"
    if status and status != "TRADING":
        return "status_not_trading"
    if _is_leveraged_token(base):
        return "leveraged_token"
    if not base or not symbol:
        return "missing_fields"
    return None


def validate_binance_payload(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, list) or not payload:
        raise InvalidCacheError("binance cache must be a non-empty list")
    rows: list[dict[str, Any]] = []
    for item in payload:
        if not isinstance(item, dict) or "symbol" not in item or "base" not in item or "quote" not in item:
            raise InvalidCacheError("binance row missing symbol/base/quote")
        rows.append(item)
    return rows


def load_binance_cache(path: Path) -> list[dict[str, Any]]:
    return validate_binance_payload(load_json(path))


def fetch_binance_spot_markets(exchange: ccxt.binance | None = None) -> list[dict[str, Any]]:
    client = exchange or create_public_binance()
    raw = client.load_markets()
    return [normalize_market(m) for m in raw.values()]


def load_or_fetch_binance_markets(
    cache_path: Path,
    *,
    refresh: bool = False,
    fetch_fn: Callable[[], list[dict[str, Any]]] | None = None,
) -> list[dict[str, Any]]:
    fetch = fetch_fn or fetch_binance_spot_markets
    cached: list[dict[str, Any]] | None = None
    try:
        cached = load_binance_cache(cache_path)
    except InvalidCacheError:
        cached = None

    if cached is not None and not refresh:
        log.info("using binance cache path=%s rows=%s", cache_path, len(cached))
        return cached

    try:
        rows = fetch()
        rows = validate_binance_payload(rows)
        atomic_write_json(cache_path, rows)
        log.info("wrote binance cache path=%s rows=%s", cache_path, len(rows))
        return rows
    except Exception as exc:
        if cached is not None:
            log.warning("binance fetch failed; using cache error=%s", exc)
            return cached
        raise RuntimeError("binance fetch failed and no valid cache exists") from exc

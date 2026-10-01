from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class MatchOverride:
    ticker: str
    coingecko_id: str
    binance_symbol: str


@dataclass(frozen=True)
class MatchPolicy:
    min_auto_ticker_length: int
    require_override: frozenset[str]
    overrides: dict[str, MatchOverride]

    def requires_override(self, ticker: str) -> bool:
        key = ticker.upper()
        return len(key) < self.min_auto_ticker_length or key in self.require_override

    def override_for(self, ticker: str) -> MatchOverride | None:
        return self.overrides.get(ticker.upper())


def load_match_policy(path: Path) -> MatchPolicy:
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    min_len = int(data.get("min_auto_ticker_length", 3))
    require = {str(item).upper() for item in data.get("require_override", [])}
    overrides: dict[str, MatchOverride] = {}
    for item in data.get("overrides", []):
        ticker = str(item["ticker"]).upper()
        overrides[ticker] = MatchOverride(
            ticker=ticker,
            coingecko_id=str(item["coingecko_id"]).lower(),
            binance_symbol=str(item["binance_symbol"]),
        )
    return MatchPolicy(
        min_auto_ticker_length=min_len,
        require_override=frozenset(require),
        overrides=overrides,
    )

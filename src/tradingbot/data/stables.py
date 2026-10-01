from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class StableDenylist:
    bases: frozenset[str]
    coingecko_ids: frozenset[str]

    def blocks_base(self, base: str) -> bool:
        return base.upper() in self.bases

    def blocks_id(self, coingecko_id: str) -> bool:
        return coingecko_id.lower() in self.coingecko_ids


def load_stables(path: Path) -> StableDenylist:
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    bases = {str(item).upper() for item in data.get("bases", [])}
    ids = {str(item).lower() for item in data.get("coingecko_ids", [])}
    return StableDenylist(bases=frozenset(bases), coingecko_ids=frozenset(ids))

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

from tradingbot.strategy.ma_crossover import MACrossoverParams


@dataclass(frozen=True)
class StrategyConfig:
    name: str
    fast: int
    slow: int


def load_strategy_config(path: Path) -> StrategyConfig:
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    block = data.get("strategy", data)
    name = str(block.get("name", "ma_crossover"))
    if name != "ma_crossover":
        raise ValueError(f"unsupported strategy {name!r}")
    params = MACrossoverParams(fast=int(block["fast"]), slow=int(block["slow"]))
    return StrategyConfig(name=name, fast=params.fast, slow=params.slow)

from pathlib import Path

import pytest

from tradingbot.strategy.config import load_strategy_config
from tradingbot.strategy.ma_crossover import MACrossover, MACrossoverParams


def test_load_strategy_config_from_toml(tmp_path: Path) -> None:
    path = tmp_path / "strategy.toml"
    path.write_text(
        '[strategy]\nname = "ma_crossover"\nfast = 20\nslow = 50\n',
        encoding="utf-8",
    )
    cfg = load_strategy_config(path)
    assert cfg.name == "ma_crossover"
    assert cfg.fast == 20
    assert cfg.slow == 50
    strategy = MACrossover(MACrossoverParams(fast=cfg.fast, slow=cfg.slow))
    assert strategy.name == "ma_crossover"


def test_load_repo_strategy_config() -> None:
    root = Path(__file__).resolve().parents[2]
    cfg = load_strategy_config(root / "configs" / "strategy.toml")
    assert cfg.name == "ma_crossover"
    assert cfg.fast == 20
    assert cfg.slow == 50


def test_load_strategy_config_rejects_unknown_name(tmp_path: Path) -> None:
    path = tmp_path / "strategy.toml"
    path.write_text('[strategy]\nname = "unknown"\nfast = 2\nslow = 3\n', encoding="utf-8")
    with pytest.raises(ValueError, match="unsupported strategy"):
        load_strategy_config(path)


def test_load_strategy_config_rejects_invalid_params(tmp_path: Path) -> None:
    path = tmp_path / "strategy.toml"
    path.write_text('[strategy]\nname = "ma_crossover"\nfast = 50\nslow = 20\n', encoding="utf-8")
    with pytest.raises(ValueError, match="fast"):
        load_strategy_config(path)

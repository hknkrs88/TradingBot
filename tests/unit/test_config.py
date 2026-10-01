from pathlib import Path

import pytest
from pydantic import ValidationError

from tradingbot.config import Settings, load_settings


def test_defaults_are_paper_binance_testnet() -> None:
    settings = Settings()
    assert settings.mode == "paper"
    assert settings.exchange_id == "binance"
    assert settings.sandbox is True
    assert settings.market_type == "spot"


def test_paper_rejects_sandbox_false() -> None:
    with pytest.raises(ValidationError):
        Settings(mode="paper", sandbox=False)


def test_load_paper_toml() -> None:
    settings = load_settings(Path("configs/paper.toml"))
    assert settings.mode == "paper"
    assert settings.sandbox is True
    assert settings.symbol == "BTC/USDT"
    assert settings.universe_path == "data/processed/universe.json"
    assert settings.universe_match_path == "configs/universe_match.toml"

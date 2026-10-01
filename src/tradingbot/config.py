from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

Mode = Literal["paper", "live"]
MarketType = Literal["spot"]


class Settings(BaseSettings):
    mode: Mode = "paper"
    exchange_id: str = "binance"
    sandbox: bool = True
    market_type: MarketType = "spot"
    symbol: str = "BTC/USDT"
    universe_path: str = "data/processed/universe.json"
    universe_match_path: str = "configs/universe_match.toml"
    binance_api_key: str = ""
    binance_api_secret: str = Field(default="", repr=False)

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @model_validator(mode="after")
    def paper_requires_sandbox(self) -> Settings:
        if self.mode == "paper" and not self.sandbox:
            raise ValueError("paper mode requires sandbox=true (Binance testnet)")
        if self.exchange_id != "binance":
            raise ValueError("only binance is supported in this skeleton")
        if self.market_type != "spot":
            raise ValueError("only spot is supported in this skeleton")
        return self


def load_settings(config_path: Path | None = None) -> Settings:
    path = config_path or Path("configs/paper.toml")
    data: dict = {}
    if path.is_file():
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    return Settings(**data)

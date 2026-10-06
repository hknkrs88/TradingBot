from __future__ import annotations

from typing import Protocol

from tradingbot.strategy.models import CandleSeries, Signal


class Strategy(Protocol):
    name: str

    def generate(self, series: CandleSeries) -> list[Signal]:
        ...

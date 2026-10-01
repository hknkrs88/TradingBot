import pytest

from tradingbot.broker.binance import create_binance_exchange
from tradingbot.config import Settings

pytestmark = pytest.mark.integration


@pytest.mark.network
def test_public_ticker_on_testnet() -> None:
    exchange = create_binance_exchange(Settings())
    ticker = exchange.fetch_ticker("BTC/USDT")
    assert ticker.get("last") is not None

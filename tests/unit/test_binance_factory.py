import pytest

from tradingbot.broker.binance import LiveTradingDisabledError, create_binance_exchange
from tradingbot.config import Settings
from tradingbot.execution import OrdersDisabledError, place_order


def test_factory_uses_testnet_urls() -> None:
    exchange = create_binance_exchange(Settings())
    api = exchange.urls.get("api") or {}
    public = str(api.get("public", ""))
    private = str(api.get("private", ""))
    assert "testnet.binance.vision" in public
    assert "testnet.binance.vision" in private


def test_live_mode_refused() -> None:
    settings = Settings(mode="live", sandbox=True)
    with pytest.raises(LiveTradingDisabledError):
        create_binance_exchange(settings)


def test_orders_are_disabled() -> None:
    with pytest.raises(OrdersDisabledError):
        place_order(symbol="BTC/USDT", side="buy", amount=0.001)

from tradingbot.data.ohlcv.binance_ohlcv import BinancePublicOHLCVProvider
from tradingbot.data.ohlcv.timeframes import UnsupportedTimeframeError
import pytest


def test_provider_uses_public_binance_not_testnet() -> None:
    provider = BinancePublicOHLCVProvider()
    api = provider.exchange.urls.get("api") or {}
    public = str(api.get("public", ""))
    private = str(api.get("private", ""))
    assert "api.binance.com" in public
    assert "testnet.binance.vision" not in public
    assert "testnet.binance.vision" not in private


def test_provider_rejects_15m_before_fetch() -> None:
    class Boom:
        def fetch_ohlcv(self, *args, **kwargs):
            raise AssertionError("must not call exchange")

    provider = BinancePublicOHLCVProvider(exchange=Boom())
    with pytest.raises(UnsupportedTimeframeError):
        provider.fetch_ohlcv("BTC/USDT", "15m", limit=10)

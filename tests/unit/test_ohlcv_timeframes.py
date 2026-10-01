import pytest

from tradingbot.data.ohlcv.timeframes import UnsupportedTimeframeError, timeframe_ms
from tradingbot.data.ohlcv.sync import load_ohlcv_config


def test_allowed_timeframes() -> None:
    assert timeframe_ms("1h") == 3_600_000
    assert timeframe_ms("4h") == 14_400_000


def test_15m_rejected() -> None:
    with pytest.raises(UnsupportedTimeframeError):
        timeframe_ms("15m")


def test_ohlcv_config_rejects_15m(tmp_path) -> None:
    path = tmp_path / "ohlcv.toml"
    path.write_text(
        'provider = "binance_public"\ntimeframes = ["15m"]\ncandle_limit = 100\n'
        'raw_dir = "r"\nprocessed_dir = "p"\nretry_max = 1\nbackoff_seconds = 0.1\n'
        "drop_incomplete = true\n",
        encoding="utf-8",
    )
    with pytest.raises(UnsupportedTimeframeError):
        load_ohlcv_config(path)

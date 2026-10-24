from pathlib import Path

from tradingbot.portfolio_sim.models import PortfolioSimConfig

SRC = Path(__file__).resolve().parents[2] / "src" / "tradingbot" / "portfolio_sim"


def test_portfolio_sim_source_has_no_order_imports() -> None:
    forbidden = (
        "tradingbot.broker",
        "tradingbot.execution",
        "place_order",
        "create_binance",
        "ccxt",
    )
    for path in SRC.glob("*.py"):
        text = path.read_text(encoding="utf-8")
        for token in forbidden:
            assert token not in text, f"{path.name} contains {token}"


def test_run_backtest_export_unchanged() -> None:
    import inspect

    from tradingbot.backtest.engine import run_backtest
    from tradingbot.backtest.models import BacktestConfig

    params = inspect.signature(run_backtest).parameters
    assert list(params) == ["series", "signals", "config"]
    ann = run_backtest.__annotations__
    assert ann["series"] in {"CandleSeries", "tradingbot.strategy.models.CandleSeries"}
    assert "Sequence" in str(ann["signals"]) or "Signal" in str(ann["signals"])
    assert ann["config"] in {"BacktestConfig", "tradingbot.backtest.models.BacktestConfig"}
    assert ann["return"] in {"BacktestResult", "tradingbot.backtest.models.BacktestResult"}
    assert BacktestConfig.__dataclass_fields__["commission_rate"]


def test_config_required_cash_and_commission() -> None:
    config = PortfolioSimConfig(initial_cash=1_000.0, commission_rate=0.001)
    assert config.initial_cash == 1_000.0
    assert config.commission_rate == 0.001
    assert config.net_profit_multiple == 1.01
    assert config.stop_loss_multiple is None

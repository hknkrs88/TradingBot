from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src" / "tradingbot" / "indicators"


def test_indicators_source_has_no_trading_or_scoring_imports() -> None:
    forbidden = (
        "tradingbot.broker",
        "tradingbot.execution",
        "tradingbot.portfolio_sim",
        "tradingbot.selection.rank_score",
        "select_top_4h",
        "tradingbot.backtest.engine",
        "place_order",
        "create_binance",
        "ccxt",
    )
    for path in SRC.glob("*.py"):
        text = path.read_text(encoding="utf-8")
        for token in forbidden:
            assert token not in text, f"{path.name} contains {token}"


def test_indicators_package_does_not_reexport_strategy_or_sim() -> None:
    import tradingbot.indicators as indicators

    blocked = (
        "MACrossover",
        "run_portfolio_sim",
        "run_backtest",
        "rank_features_4h",
        "score_features_4h",
        "select_top_4h_candidates",
        "Signal",
    )
    exported = set(indicators.__all__)
    for name in blocked:
        assert name not in exported
        assert not hasattr(indicators, name)

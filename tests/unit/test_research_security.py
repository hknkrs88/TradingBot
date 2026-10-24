from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src" / "tradingbot" / "research"


def test_research_source_has_no_broker_sim_or_ccxt() -> None:
    forbidden = (
        "tradingbot.broker",
        "tradingbot.execution",
        "tradingbot.portfolio_sim",
        "tradingbot.backtest.engine",
        "place_order",
        "create_binance",
        "ccxt",
        "select_top_4h",
        "tradingbot.selection.rank_score",
    )
    for path in SRC.glob("*.py"):
        text = path.read_text(encoding="utf-8")
        for token in forbidden:
            assert token not in text, f"{path.name} contains {token}"

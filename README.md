# TradingBot

Paper-first bot. Default venue is **Binance spot testnet** (`https://testnet.binance.vision`). No live trading, no order placement yet.

## Setup

```powershell
C:\TradingBot\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
copy .env.example .env
```

Testnet keys (optional, only for account endpoints): [Binance Spot Test Network](https://testnet.binance.vision). These are not live Binance keys.

## Run

Public ticker against testnet (no orders):

```powershell
python -m tradingbot.main
# or
python scripts\smoke_binance_testnet.py
```

Unit tests (no network):

```powershell
pytest tests\unit
```

## Coin universe (public metadata only)

Derived list of CoinGecko top-250 ∩ Binance **live** Spot USDT pairs:

```powershell
python scripts\build_universe.py
python scripts\build_universe.py --refresh
```

- CoinGecko: one `GET /api/v3/coins/markets` (250 rows), cached at `data/raw/coingecko_markets.json`
- Binance: unauthenticated `load_markets()` on live public REST (no API keys, no sandbox, no orders), cached at `data/raw/binance_spot_markets.json`
- Output: `data/processed/universe.json` (path also in `configs/paper.toml` as `universe_path`)
- Stables denylist: `configs/stables.toml`
- If a fetch fails and a valid cache exists, the cache is used. If both fail, the script exits non-zero and does **not** replace `universe.json`.

This is not trading. The paper bot still uses Binance **testnet** for tickers; universe construction is listing metadata only.

Root `test_coingecko.py` is a leftover scratch script and is not part of this pipeline.

## Layout

- `configs/paper.toml` — default paper / sandbox + `universe_path`
- `configs/live.toml.example` — example only; live mode is refused in code
- `src/tradingbot/broker/binance.py` — ccxt client with `set_sandbox_mode(True)`
- `src/tradingbot/data/` — universe builder (CoinGecko + public live markets)

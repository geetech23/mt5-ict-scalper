# mt5-ict-scalper

ICT / Smart Money Concepts scalping bot for **TradeLocker** (primary) with optional **MT5** support,
session filters (London / NY / Asia), risk management, backtesting and a live web dashboard.

> ⚠️ Trading is risky. Start in simulation mode, then a demo account. Nothing here is financial advice.

## Quick start
```bash
pip install -r requirements.txt
python main.py          # first run launches a setup wizard, then opens the dashboard (http://127.0.0.1:8080)
```
Copy `.env.example` to `.env` to configure manually (`.env` is gitignored; never commit credentials).

- `BROKER=tradelocker|mt5|sim` – broker selection.
- `SIMULATION_MODE=true` (default) – no real orders are sent. With broker credentials, real prices are used
  for paper trading; otherwise synthetic prices are generated.
- `TL_ENVIRONMENT=demo|live` – TradeLocker demo/live toggle. Set `SIMULATION_MODE=false` to place real orders.

## Layout
| Path | Purpose |
|---|---|
| `config.py` | Config from `.env` (sessions, risk, ICT thresholds, timeframes) |
| `brokers/` | `BaseConnector` interface; TradeLocker, MT5 (optional), simulated connectors |
| `ict/` | `structures.py` (data classes), `analyzer.py` (swings, HH/HL/LH/LL, BOS, ChoCH, OB, FVG, sweeps, equal H/L), `signals.py` |
| `session_manager.py` | Session detection, per-session symbols, countdown to next change (UTC) |
| `strategy.py`, `execution.py`, `risk_manager.py` | Signals, orders + breakeven/trailing, sizing + limits |
| `backtest.py` | `python backtest.py --symbol EURUSD --bars 3000 [--walk-forward 4]` |
| `dashboard/` | Flask dashboard (`/api/state` JSON + responsive UI) |
| `journal.py`, `utils/` | CSV journal (`logs/trade_journal.csv`), rotating logs (`logs/bot.log`), helpers |

## Risk rules
Position size from equity × `RISK_PER_TRADE_PCT` × `POSITION_SIZE_MULTIPLIER`; minimum RR 1:2 enforced;
daily loss circuit breaker; max consecutive losses; max trades per session; breakeven and trailing stops.

## Tests
```bash
python -m pytest
```

## Notes
The TradeLocker connector uses the official `tradelocker` package (REST polling; WebSocket streaming is not
implemented). Verify it against a demo account before going live. To add a broker, subclass `BaseConnector`
and register it in `brokers/__init__.py`.

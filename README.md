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

## TradeLocker

`brokers/tradelocker_connector.py` wraps the official [`tradelocker`](https://pypi.org/project/tradelocker/) package.

**Setup** – in `.env` (or via the `python main.py` wizard):
```
BROKER=tradelocker
TL_ENVIRONMENT=demo        # demo | live
TL_EMAIL=...               # your TradeLocker login
TL_PASSWORD=...
TL_SERVER=...              # server name shown by your broker
TL_ACC_NUM=0               # account number if you have several (0 = first)
SIMULATION_MODE=true       # true = paper trade on real TradeLocker prices, no orders sent
```
Set `SIMULATION_MODE=false` to place real orders (on the demo or live account chosen by `TL_ENVIRONMENT`).

**What the connector does**
- Authenticates with your credentials and reconnects automatically (a failed call triggers a reconnect and is then reported as an error).
- Resolves symbol -> instrument ID once and caches it.
- Fetches candles (M1-D1) and bid/ask quotes by polling REST; WebSocket streaming is not implemented.
- Reads account balance/equity and open positions.
- Places market orders with absolute SL/TP, modifies SL/TP (used for breakeven and trailing), and closes positions.
- Pip size / pip value use built-in defaults (FX, JPY pairs, XAU, XAG), not broker data - check lot sizing for your instruments.

**Recommended path:** simulation with credentials -> demo with `SIMULATION_MODE=false` -> live.

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

## Adaptive risk management

Risk per trade scales with realized daily growth: 1.0% (<5%), 1.25% (5-10%), 1.5% (10-15%), and new
entries stop at +15%. Escalation requires >50% win rate, positive expectancy and no peak drawdown over
the day's closed trades (`GATE_MIN_TRADES`). Modes: `NORMAL`, `REDUCED` (0.5% after equity falls 2% from the
daily peak until `RECOVERY_TRADES` net-positive closed trades), `ELEVATED` (1.5%). The hard daily loss
limit (`DAILY_LOSS_LIMIT_PCT`, default 3%, realized + floating) overrides everything, and open risk is
capped by `MAX_AGGREGATE_RISK_PCT`. Consecutive losses are only logged as warnings (`MAX_CONSECUTIVE_LOSSES`)
- trading is never paused. All thresholds are in `config.py` / `.env` (see `RISK_*`, `DRAWDOWN_FROM_PEAK_PCT`, etc.).

Backtest: run `Backtester` (`backtest.py`) on candle data with `ADAPTIVE_RISK=true` vs `false` and compare
profit factor, drawdown and expectancy; validate on a demo account before going live.

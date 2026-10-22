"""Backtesting engine: replays candles through the ICT strategy and reports summary stats."""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence

from brokers.base_connector import SymbolSpec, default_spec
from config import Config
from ict.structures import Candle
from risk_manager import RiskManager
from session_manager import SessionManager
from strategy import evaluate
from utils.logger import get_logger

log = get_logger("backtest")


@dataclass
class BTTrade:
    symbol: str
    side: str
    entry_time: Any
    entry: float
    sl: float
    tp: float
    lots: float
    exit: float = 0.0
    pnl: float = 0.0
    reason: str = ""


class Backtester:
    def __init__(self, cfg: Config, balance: float = 10_000.0, window: int = 150, warmup: int = 40) -> None:
        self.cfg, self.start_balance, self.window, self.warmup = cfg, balance, window, warmup
        self.sessions = SessionManager.from_config(cfg)

    def run(self, candles: Sequence[Candle], symbol: str = "EURUSD", spec: Optional[SymbolSpec] = None,
            htf_candles: Optional[Sequence[Candle]] = None) -> Dict[str, Any]:
        spec = spec or default_spec(symbol)
        risk = RiskManager(self.cfg)
        balance = self.start_balance
        trades: List[BTTrade] = []
        open_t: Optional[BTTrade] = None
        equity_curve = [balance]
        day, day_pnl, day_losses = None, 0.0, 0
        sess_counts: Dict[str, int] = {}

        for i in range(self.warmup, len(candles)):
            c = candles[i]
            if c.time.date() != day:
                day, day_pnl, day_losses = c.time.date(), 0.0, 0
                risk.start_day(balance, c.time)
            if open_t:
                hit = self._check_exit(open_t, c)
                if hit:
                    price, why = hit
                    diff = price - open_t.entry if open_t.side == "buy" else open_t.entry - price
                    open_t.exit, open_t.reason = price, why
                    open_t.pnl = diff / spec.pip_size * spec.pip_value_per_lot * open_t.lots
                    balance += open_t.pnl
                    day_pnl += open_t.pnl
                    day_losses = day_losses + 1 if open_t.pnl <= 0 else 0
                    trades.append(open_t)
                    open_t_pnl, open_t = open_t.pnl, None
                    equity_curve.append(balance)
                    risk.record_trade(open_t_pnl, c.time)
                    risk.update_equity(balance, c.time)
            if open_t or i + 1 >= len(candles):
                continue
            active = self.sessions.tradable_sessions(symbol, c.time)
            if not active:
                continue
            key = self.sessions.session_key(active[0], c.time)
            if sess_counts.get(key, 0) >= self.cfg.max_trades_per_session:
                continue
            if day_pnl <= -balance * self.cfg.daily_loss_limit_pct / 100 or not risk.can_trade(now=c.time)[0]:
                continue
            lo = max(0, i + 1 - self.window)
            htf = [h for h in htf_candles if h.time <= c.time][-100:] if htf_candles else None
            _, sig = evaluate(self.cfg, symbol, spec.pip_size, candles[lo:i + 1], htf)
            if not sig:
                continue
            nxt = candles[i + 1]  # fill at next open
            entry = nxt.open
            if (sig.side == "buy" and not sig.sl < entry < sig.tp) or (sig.side == "sell" and not sig.tp < entry < sig.sl):
                continue
            if not risk.check_rr(entry, sig.sl, sig.tp):
                continue
            lots = risk.position_size(balance, entry, sig.sl, spec)
            if lots <= 0:
                continue
            sess_counts[key] = sess_counts.get(key, 0) + 1
            open_t = BTTrade(symbol, sig.side, nxt.time, entry, sig.sl, sig.tp, lots)
        return self.summarize(trades, equity_curve)

    @staticmethod
    def _check_exit(t: BTTrade, c: Candle):
        if t.side == "buy":
            if c.low <= t.sl:
                return t.sl, "SL"
            if c.high >= t.tp:
                return t.tp, "TP"
        else:
            if c.high >= t.sl:
                return t.sl, "SL"
            if c.low <= t.tp:
                return t.tp, "TP"
        return None

    def summarize(self, trades: List[BTTrade], equity_curve: List[float]) -> Dict[str, Any]:
        wins = [t.pnl for t in trades if t.pnl > 0]
        losses = [t.pnl for t in trades if t.pnl <= 0]
        gross_loss = -sum(losses)
        peak, max_dd = equity_curve[0], 0.0
        for e in equity_curve:
            peak = max(peak, e)
            max_dd = max(max_dd, (peak - e) / peak * 100 if peak else 0.0)
        return {"trades": len(trades), "wins": len(wins), "losses": len(losses),
                "win_rate": round(100 * len(wins) / len(trades), 1) if trades else 0.0,
                "net_pnl": round(sum(t.pnl for t in trades), 2),
                "profit_factor": round(sum(wins) / gross_loss, 2) if gross_loss else (float("inf") if wins else 0.0),
                "max_drawdown_pct": round(max_dd, 2),
                "final_balance": round(equity_curve[-1], 2)}

    def walk_forward(self, candles: Sequence[Candle], symbol: str = "EURUSD", windows: int = 4,
                     spec: Optional[SymbolSpec] = None) -> List[Dict[str, Any]]:
        """Run the strategy on consecutive, non-overlapping segments (out-of-sample style)."""
        if windows < 1:
            raise ValueError("windows must be >= 1")
        size = len(candles) // windows
        results = []
        for w in range(windows):
            seg = candles[w * size:(w + 1) * size if w < windows - 1 else len(candles)]
            res = self.run(seg, symbol, spec)
            res["window"] = w + 1
            results.append(res)
        return results


def print_summary(res: Dict[str, Any]) -> None:
    print("\n=== Backtest summary ===")
    for k, v in res.items():
        print(f"{k:>18}: {v}")


def main() -> None:
    from brokers import create_connector
    from utils.logger import setup_logging
    setup_logging()
    ap = argparse.ArgumentParser(description="ICT scalper backtest")
    ap.add_argument("--symbol", default=None)
    ap.add_argument("--bars", type=int, default=3000)
    ap.add_argument("--walk-forward", type=int, default=0, help="number of walk-forward windows")
    args = ap.parse_args()
    cfg = Config.from_env()
    symbol = args.symbol or cfg.symbols[0]
    conn = create_connector(cfg)
    if not conn.connect():
        raise SystemExit("❌ Could not connect to broker for historical data")
    candles = conn.get_candles(symbol, cfg.timeframe, args.bars)
    bt = Backtester(cfg)
    spec = conn.get_symbol_spec(symbol)
    if args.walk_forward:
        for r in bt.walk_forward(candles, symbol, args.walk_forward, spec):
            print_summary(r)
    else:
        print_summary(bt.run(candles, symbol, spec))


if __name__ == "__main__":
    main()

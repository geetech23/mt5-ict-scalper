"""Order placement and open-position management (breakeven / trailing)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional

from brokers.base_connector import BaseConnector, Position
from config import Config
from ict.structures import Signal
from journal import Journal
from risk_manager import RiskManager
from utils.logger import get_logger
from utils.validators import validate_order

log = get_logger("execution")


@dataclass
class TradeMeta:
    symbol: str
    side: str
    entry: float
    initial_sl: float
    tp: float
    reason: str
    last_price: float = 0.0
    last_profit: float = 0.0
    breakeven_done: bool = False
    group: str = ""  # legs of one scalp (partial TP) share a group id
    first_leg: bool = False


class ExecutionEngine:
    def __init__(self, cfg: Config, connector: BaseConnector, risk: RiskManager, journal: Journal) -> None:
        self.cfg, self.conn, self.risk, self.journal = cfg, connector, risk, journal
        self.meta: Dict[str, TradeMeta] = {}

    def has_open(self, symbol: str) -> bool:
        return any(m.symbol == symbol for m in self.meta.values())

    def enter(self, sig: Signal, session_key: str = "") -> Optional[str]:
        ok, why = self.risk.can_trade(session_key)
        if not ok:
            log.warning("⚠️ Trade blocked: %s", why)
            return None
        if not self.risk.check_rr(sig.entry, sig.sl, sig.tp):
            log.warning("⚠️ Trade blocked: RR %.2f below minimum %.1f", sig.rr, self.cfg.min_rr)
            return None
        if self.has_open(sig.symbol):
            return None
        spec = self.conn.get_symbol_spec(sig.symbol)
        lots = self.risk.position_size(self.conn.get_account().equity, sig.entry, sig.sl, spec)
        if lots <= 0:
            return None
        try:
            validate_order(sig.side, lots, sig.sl, sig.tp, sig.entry)
        except ValueError as exc:
            log.error("❌ Invalid order: %s", exc)
            return None
        first_lots, run_lots = (lots, 0.0)
        if sig.tp1 is not None:
            first_lots, run_lots = self.risk.split_lots(lots, spec)
        first_tp = sig.tp1 if run_lots > 0 and sig.tp1 is not None else sig.tp
        pid = self.conn.place_order(sig.symbol, sig.side, first_lots, sig.sl, first_tp)
        if not pid:
            log.error("❌ Order rejected for %s", sig.symbol)
            return None
        reason = "; ".join(sig.reasons)
        group = pid if run_lots > 0 else ""
        self.meta[pid] = TradeMeta(sig.symbol, sig.side, sig.entry, sig.sl, first_tp, reason,
                                   group=group, first_leg=run_lots > 0)
        if run_lots > 0:
            rid = self.conn.place_order(sig.symbol, sig.side, run_lots, sig.sl, sig.tp)
            if rid:
                self.meta[rid] = TradeMeta(sig.symbol, sig.side, sig.entry, sig.sl, sig.tp, reason, group=group)
            else:
                log.warning("Runner leg rejected for %s; first leg keeps TP1", sig.symbol)
        if session_key:
            self.risk.record_entry(session_key)
        self.journal.record("OPEN", sig.symbol, sig.side, sig.entry, sig.sl, sig.tp, reason=reason)
        log.info("✅ Opened %s %s %.2f lots (id %s)", sig.side.upper(), sig.symbol, lots, pid)
        return pid

    def manage(self) -> List[Position]:
        """Apply breakeven/trailing, and journal positions closed since last call."""
        positions = self.conn.get_positions()
        live = {p.id: p for p in positions}
        for pid, m in list(self.meta.items()):
            p = live.get(pid)
            if p is None:
                self._on_closed(pid, m)
                continue
            m.last_price, m.last_profit = p.current_price or m.last_price, p.profit
            self._trail(p, m)
        return positions

    def _trail(self, p: Position, m: TradeMeta) -> None:
        risk = abs(m.entry - m.initial_sl)
        if risk <= 0 or not p.current_price:
            return
        buy = m.side == "buy"
        gain = (p.current_price - m.entry) if buy else (m.entry - p.current_price)
        new_sl: Optional[float] = None
        pip = self.conn.get_symbol_spec(m.symbol).pip_size
        be = (m.entry + pip if buy else m.entry - pip) if self.cfg.scalp_mode else m.entry
        if self.cfg.scalp_mode and self.cfg.scalp_trail_momentum and gain >= 2 * risk:
            cand = m.entry + risk if buy else m.entry - risk  # lock in 1R once price runs to 2R
            if (buy and cand > p.sl) or (not buy and cand < p.sl):
                new_sl = cand
        elif gain >= self.cfg.trailing_r * risk:
            cand = p.current_price - risk if buy else p.current_price + risk
            if (buy and cand > p.sl) or (not buy and cand < p.sl):
                new_sl = cand
        elif gain >= self.cfg.breakeven_r * risk and not m.breakeven_done:
            if (buy and p.sl < be) or (not buy and p.sl > be):
                new_sl = be
        if new_sl is not None and self.conn.modify_position(p.id, sl=new_sl):
            m.breakeven_done = True
            self.journal.record("MODIFY", m.symbol, m.side, sl=round(new_sl, 5), reason="breakeven/trail")
            log.info("🔒 %s SL moved to %.5f", m.symbol, new_sl)

    def _protect_runner(self, closed: TradeMeta) -> None:
        """First scalp leg hit TP1: move the runner's SL to entry + 1 pip."""
        pip = self.conn.get_symbol_spec(closed.symbol).pip_size
        buy = closed.side == "buy"
        be = closed.entry + pip if buy else closed.entry - pip
        live = {p.id: p for p in self.conn.get_positions()}
        for pid, m in self.meta.items():
            p = live.get(pid)
            if m.group == closed.group and not m.first_leg and p is not None and \
                    ((buy and p.sl < be) or (not buy and p.sl > be)) and self.conn.modify_position(pid, sl=be):
                m.breakeven_done = True
                self.journal.record("MODIFY", m.symbol, m.side, sl=round(be, 5), reason="TP1 hit: runner to BE+1pip")
                log.info("🔒 %s runner SL moved to %.5f", m.symbol, be)

    def _on_closed(self, pid: str, m: TradeMeta) -> None:
        res = self.conn.closed_result(pid)
        exit_price, pnl = res if res else (m.last_price, m.last_profit)
        self.risk.record_trade(pnl)
        if m.first_leg and pnl > 0:
            self._protect_runner(m)
        reason = "TP/SL hit" if res else "closed (last known)"
        self.journal.record("CLOSE", m.symbol, m.side, m.entry, m.initial_sl, m.tp,
                            round(exit_price, 5), round(pnl, 2), reason)
        log.info("%s Closed %s %s P&L %.2f", "✅" if pnl > 0 else "❌", m.side.upper(), m.symbol, pnl)
        del self.meta[pid]

    def close_all(self) -> None:
        for p in self.conn.get_positions():
            self.conn.close_position(p.id)
        self.manage()

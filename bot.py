"""Main trading loop; also exposes a thread-safe snapshot for the dashboard."""
from __future__ import annotations

import threading
import time
from typing import Any, Dict, List, Optional

from brokers.base_connector import BaseConnector
from config import Config
from execution import ExecutionEngine
from ict.structures import ICTAnalysis, Signal
from journal import Journal
from risk_manager import RiskManager
from session_manager import SessionManager
from strategy import Strategy
from utils.logger import get_logger
from utils.time_utils import utc_now

log = get_logger("bot")


class TradingBot:
    def __init__(self, cfg: Config, connector: BaseConnector, journal: Optional[Journal] = None,
                 poll_seconds: float = 5.0) -> None:
        self.cfg, self.conn = cfg, connector
        self.journal = journal or Journal()
        self.sessions = SessionManager.from_config(cfg)
        self.risk = RiskManager(cfg)
        self.exec = ExecutionEngine(cfg, connector, self.risk, self.journal)
        self.strategy = Strategy(cfg, connector)
        self.poll = poll_seconds
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._analyses: Dict[str, ICTAnalysis] = {}
        self._signals: Dict[str, Optional[Signal]] = {}
        self._account: Dict[str, Any] = {}
        self._positions: List[Dict[str, Any]] = []
        self._last_scan: Dict[str, float] = {}
        self._tf_seconds = max(30.0, self.poll * 6)

    def stop(self) -> None:
        self._stop.set()

    def run(self) -> None:
        if not self.conn.is_connected() and not self.conn.connect():
            log.error("❌ Could not connect to broker")
        log.info("🚀 Bot started | broker=%s sessions=%s symbols=%s", self.conn.name,
                 ",".join(self.cfg.enabled_sessions), ",".join(self.cfg.symbols))
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception as exc:  # noqa: BLE001 - loop must survive transient errors
                log.error("❌ Loop error: %s", exc)
                if not self.conn.is_connected():
                    log.warning("⚠️ Reconnecting...")
                    self.conn.reconnect()
            self._stop.wait(self.poll)

    def tick(self) -> None:
        now = utc_now()
        acct = self.conn.get_account()
        self.risk.start_day(acct.equity)
        positions = self.exec.manage()
        with self._lock:
            self._account = {"balance": acct.balance, "equity": acct.equity, "currency": acct.currency}
            self._positions = [vars(p).copy() for p in positions]
        for symbol in self.cfg.symbols:
            tradable = self.sessions.tradable_sessions(symbol, now)
            if not tradable:
                continue
            if time.time() - self._last_scan.get(symbol, 0) < self._tf_seconds:
                continue
            self._last_scan[symbol] = time.time()
            analysis, sig = self.strategy.scan(symbol)
            with self._lock:
                self._analyses[symbol], self._signals[symbol] = analysis, sig
            if sig:
                key = self.sessions.session_key(tradable[0], now)
                self.exec.enter(sig, key)

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            analyses = {s: self._summ(a) for s, a in self._analyses.items()}
            signals = {s: (g.to_dict() if g else None) for s, g in self._signals.items()}
            account, positions = dict(self._account), list(self._positions)
        trading_ok, why = self.risk.can_trade()
        return {"time": utc_now().isoformat(timespec="seconds"), "connected": self.conn.is_connected(),
                "broker": self.conn.name, "simulation": self.cfg.simulation_mode,
                "session": self.sessions.status(), "account": account, "positions": positions,
                "signals": signals, "zones": analyses, "stats": self.journal.daily_stats(),
                "journal": self.journal.recent_trades(20),
                "risk": {"daily_pnl": round(self.risk.daily_pnl, 2),
                         "consecutive_losses": self.risk.consecutive_losses,
                         "trading_allowed": trading_ok, "reason": why}}

    @staticmethod
    def _summ(a: ICTAnalysis) -> Dict[str, Any]:
        return {"trend": a.trend.value,
                "order_blocks": [{"dir": o.direction.value, "low": o.low, "high": o.high}
                                 for o in a.order_blocks if not o.mitigated][-5:],
                "fvgs": [{"dir": g.direction.value, "bottom": g.bottom, "top": g.top}
                         for g in a.fvgs if not g.filled][-5:],
                "last_break": (f"{a.breaks[-1].kind.value} {a.breaks[-1].direction.value}" if a.breaks else None),
                "structure": [s.label for s in a.swings if s.label][-6:]}

"""Position sizing, risk limits and circuit breakers."""
from __future__ import annotations

import json
from datetime import date, datetime
from enum import Enum
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from brokers.base_connector import SymbolSpec
from config import Config
from utils.logger import get_logger
from utils.time_utils import utc_now

log = get_logger("risk")


class RiskMode(str, Enum):
    NORMAL = "NORMAL"      # risk scales with daily growth tiers (gated by performance)
    REDUCED = "REDUCED"    # risk locked at reduced_risk_pct after a drawdown from the daily peak
    ELEVATED = "ELEVATED"  # risk locked at max_normal_risk_pct after significant daily gains


class RiskManager:
    """Adaptive risk: tiers from realized daily growth, peak-equity protection, hard daily stop.

    Consecutive losses are only logged as warnings - they never pause or block trading.
    """

    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self.day: date = utc_now().date()
        self.day_start_equity: Optional[float] = None
        self.peak_equity: Optional[float] = None
        self.equity: Optional[float] = None  # last known live equity (includes floating P&L)
        self.daily_pnl = 0.0  # realized only
        self.consecutive_losses = 0
        self.trades: List[float] = []  # today's closed-trade P&L
        self.session_trades: Dict[str, int] = {}
        self.open_risk: Dict[str, float] = {}  # position id -> money at risk
        self.mode = RiskMode.NORMAL
        self._reduced_at = 0  # len(self.trades) when REDUCED was entered
        self._last_risk_pct: Optional[float] = None
        self.halted_reason = ""

    # ---- state -----------------------------------------------------------------
    def _roll_day(self, now: Optional[datetime] = None) -> None:
        today = (now or utc_now()).date()
        if today != self.day:
            self.day, self.daily_pnl, self.consecutive_losses = today, 0.0, 0
            self.day_start_equity, self.peak_equity, self.equity = None, None, None
            self.trades, self.session_trades, self.halted_reason = [], {}, ""
            self.mode, self._reduced_at, self._last_risk_pct = RiskMode.NORMAL, 0, None
            log.info("New trading day %s - risk reset to NORMAL", today)

    def start_day(self, equity: float, now: Optional[datetime] = None) -> None:
        self._roll_day(now)
        if self.day_start_equity is None:
            self.day_start_equity = self.peak_equity = self.equity = equity

    def update_equity(self, equity: float, now: Optional[datetime] = None) -> None:
        """Feed live equity (incl. floating P&L) to track the daily peak and drawdown."""
        self.start_day(equity, now)
        self.equity = equity
        if self.peak_equity is None or equity > self.peak_equity:
            self.peak_equity = equity
        self._refresh_mode()

    def record_trade(self, pnl: float, now: Optional[datetime] = None) -> None:
        self._roll_day(now)
        self.daily_pnl += pnl
        self.trades.append(pnl)
        self.consecutive_losses = self.consecutive_losses + 1 if pnl <= 0 else 0
        if self.consecutive_losses >= self.cfg.max_consecutive_losses:
            log.warning("⚠️ %d consecutive losses (daily P&L %.2f) - trading continues, no pause",
                        self.consecutive_losses, self.daily_pnl)
        else:
            log.info("Risk state: daily P&L %.2f, consecutive losses %d", self.daily_pnl, self.consecutive_losses)
        self._refresh_mode()

    def record_entry(self, session_key: str) -> None:
        self.session_trades[session_key] = self.session_trades.get(session_key, 0) + 1

    # ---- metrics ---------------------------------------------------------------
    def growth_pct(self) -> float:
        """Realized daily growth vs. starting equity (floating P&L deliberately excluded)."""
        return self.daily_pnl / self.day_start_equity * 100 if self.day_start_equity else 0.0

    def drawdown_pct(self) -> float:
        if not self.peak_equity or self.equity is None:
            return 0.0
        return max(0.0, (self.peak_equity - self.equity) / self.peak_equity * 100)

    @staticmethod
    def _favorable(pnls: List[float], min_trades: int, min_win_rate: float) -> bool:
        if len(pnls) < min_trades or not pnls:
            return False
        wins = sum(1 for p in pnls if p > 0)
        return wins / len(pnls) > min_win_rate and sum(pnls) / len(pnls) > 0

    def performance_ok(self) -> bool:
        """Escalation gate: win rate, expectancy (today's closed trades) and drawdown."""
        c = self.cfg
        return (self._favorable(self.trades, c.gate_min_trades, c.gate_min_win_rate)
                and self.drawdown_pct() < c.drawdown_from_peak_pct)

    def _set_mode(self, mode: RiskMode, why: str) -> None:
        if mode != self.mode:
            log.info("Risk mode %s -> %s (%s)", self.mode.value, mode.value, why)
            self.mode = mode

    def _refresh_mode(self) -> None:
        c = self.cfg
        if not c.adaptive_risk:
            return
        dd = self.drawdown_pct()
        if self.mode == RiskMode.REDUCED:
            since = self.trades[self._reduced_at:]
            if (dd < c.drawdown_from_peak_pct and len(since) >= c.recovery_trades
                    and self._favorable(since, c.recovery_trades, 0.0)):
                self._set_mode(RiskMode.NORMAL, f"recovered after {len(since)} trades")
            else:
                return
        elif dd >= c.drawdown_from_peak_pct:
            self._reduced_at = len(self.trades)
            self._set_mode(RiskMode.REDUCED, f"equity {dd:.2f}% below daily peak")
            return
        if self.growth_pct() >= c.risk_tier2_growth_pct and self.performance_ok():
            self._set_mode(RiskMode.ELEVATED, f"daily growth {self.growth_pct():.2f}%")
        else:
            self._set_mode(RiskMode.NORMAL, "default")

    def current_risk_pct(self) -> float:
        c = self.cfg
        if not c.adaptive_risk:
            return c.risk_per_trade_pct
        if self.mode == RiskMode.REDUCED:
            risk = c.reduced_risk_pct
        elif self.mode == RiskMode.ELEVATED:
            risk = c.max_normal_risk_pct
        else:
            risk = c.risk_per_trade_pct
            g = self.growth_pct()
            if self.performance_ok():
                if g >= c.risk_tier2_growth_pct:
                    risk = max(risk, min(c.risk_tier2_pct, c.max_normal_risk_pct))
                elif g >= c.risk_tier1_growth_pct:
                    risk = max(risk, min(c.risk_tier1_pct, c.max_normal_risk_pct))
        if risk != self._last_risk_pct:
            log.info("Risk per trade now %.2f%% (mode %s, daily growth %.2f%%)",
                     risk, self.mode.value, self.growth_pct())
            self._last_risk_pct = risk
        return risk

    # ---- open exposure -----------------------------------------------------------
    def register_open_risk(self, key: str, amount: float) -> None:
        self.open_risk[key] = amount

    def release_open_risk(self, key: str) -> None:
        self.open_risk.pop(key, None)

    def within_aggregate_cap(self, new_risk: float, equity: float) -> bool:
        total = sum(self.open_risk.values()) + new_risk
        return total <= equity * self.cfg.max_aggregate_risk_pct / 100 + 1e-9

    # ---- persistence (terminal restarts) -------------------------------------------
    def save_state(self, path: Path) -> None:
        data = {"day": self.day.isoformat(), "day_start_equity": self.day_start_equity,
                "peak_equity": self.peak_equity, "daily_pnl": self.daily_pnl, "trades": self.trades,
                "consecutive_losses": self.consecutive_losses, "session_trades": self.session_trades,
                "mode": self.mode.value, "reduced_at": self._reduced_at}
        path.write_text(json.dumps(data), encoding="utf-8")

    def load_state(self, path: Path) -> bool:
        """Restore today's state after a restart; ignores stale or corrupt files."""
        try:
            d = json.loads(path.read_text(encoding="utf-8"))
            if d["day"] != utc_now().date().isoformat():
                return False
            self.day = utc_now().date()
            self.day_start_equity, self.peak_equity = d["day_start_equity"], d["peak_equity"]
            self.daily_pnl, self.trades = d["daily_pnl"], list(d["trades"])
            self.consecutive_losses, self.session_trades = d["consecutive_losses"], d["session_trades"]
            self.mode, self._reduced_at = RiskMode(d["mode"]), d["reduced_at"]
        except (OSError, ValueError, KeyError, TypeError):
            return False
        log.info("Restored risk state: start equity %s, daily P&L %.2f, mode %s",
                 self.day_start_equity, self.daily_pnl, self.mode.value)
        return True

    # ---- checks ----------------------------------------------------------------
    def daily_loss_limit(self) -> float:
        return (self.day_start_equity or 0.0) * self.cfg.daily_loss_limit_pct / 100

    def circuit_breaker_tripped(self, now: Optional[datetime] = None) -> bool:
        """Hard daily stop (realized or floating). Consecutive losses never trip it."""
        self._roll_day(now)
        loss = -min(self.daily_pnl, (self.equity - self.day_start_equity)
                    if self.equity is not None and self.day_start_equity else self.daily_pnl)
        if self.day_start_equity and loss >= self.daily_loss_limit() > 0:
            self.halted_reason = f"Daily loss limit reached ({-loss:.2f})"
        elif self.day_start_equity and self.growth_pct() >= self.cfg.daily_profit_target_pct:
            self.halted_reason = f"Daily profit target reached ({self.growth_pct():.2f}%)"
        else:
            self.halted_reason = ""
        return bool(self.halted_reason)

    def can_trade(self, session_key: str = "", now: Optional[datetime] = None) -> Tuple[bool, str]:
        if self.circuit_breaker_tripped(now):
            return False, self.halted_reason
        if session_key and self.session_trades.get(session_key, 0) >= self.cfg.max_trades_per_session:
            return False, f"Max trades for {session_key} reached"
        return True, "ok"

    def check_rr(self, entry: float, sl: float, tp: float) -> bool:
        risk = abs(entry - sl)
        return risk > 0 and abs(tp - entry) / risk >= self.cfg.min_rr - 1e-9

    # ---- sizing ----------------------------------------------------------------
    def position_size(self, equity: float, entry: float, sl: float, spec: SymbolSpec) -> float:
        """Lots such that hitting SL loses ~risk% of equity (scaled by size multiplier)."""
        sl_pips = abs(entry - sl) / spec.pip_size + self.cfg.risk_cost_buffer_pips
        if equity <= 0 or sl_pips <= 0:
            return 0.0
        risk_amt = equity * self.current_risk_pct() / 100 * self.cfg.position_size_multiplier
        lots = risk_amt / (sl_pips * spec.pip_value_per_lot)
        lots = int(lots / spec.lot_step + 1e-9) * spec.lot_step  # round down to step
        lots = min(lots, spec.max_lot)
        if lots < spec.min_lot:
            log.warning("Computed lot %.4f below minimum %.2f - trade skipped (risk too small)",
                        lots, spec.min_lot)
            return 0.0
        log.info("Risk calc: equity=%.2f risk=%.2f sl_pips=%.1f -> %.2f lots", equity, risk_amt, sl_pips, lots)
        return round(lots, 8)

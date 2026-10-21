"""Position sizing, risk limits and circuit breakers."""
from __future__ import annotations

from datetime import date, datetime, timedelta
from enum import Enum
from typing import Dict, List, Optional, Tuple

from brokers.base_connector import SymbolSpec
from config import Config
from utils.logger import get_logger
from utils.time_utils import utc_now

log = get_logger("risk")


class RiskState(str, Enum):
    NORMAL = "NORMAL"      # risk scales with realized daily growth (base..max_risk_per_trade_pct)
    REDUCED = "REDUCED"    # risk locked at reduced_risk_pct (after drawdown from peak / post-cooldown)
    COOLDOWN = "COOLDOWN"  # no new entries until cooldown_until (UTC); open positions unaffected


class RiskManager:
    """Adaptive position sizing, daily limits and the NORMAL/REDUCED/COOLDOWN state machine.

    Reference points: ``day_start_equity`` (profit/loss targets, realized growth),
    ``peak_equity`` (trailing drawdown protection, includes floating P&L) and
    ``daily_pnl`` (realized P&L only; drives the risk tier). Hard daily loss limit
    and profit target override everything else.
    """

    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self.day: date = utc_now().date()
        self.day_start_equity: Optional[float] = None
        self.peak_equity: Optional[float] = None
        self.current_equity: Optional[float] = None
        self.daily_pnl = 0.0
        self.consecutive_losses = 0
        self.session_trades: Dict[str, int] = {}
        self.halted_reason = ""
        self.state = RiskState.NORMAL
        self.cooldown_until: Optional[datetime] = None
        self.reduced_since: Optional[datetime] = None
        self.wins_since_reduced = 0
        self.recent_pnls: List[float] = []
        self.open_risk: Dict[str, float] = {}  # position id -> risk % of equity
        self.last_risk_pct = 0.0
        self._last_tier = 0

    # ---- state -----------------------------------------------------------------
    def _roll_day(self) -> None:
        today = utc_now().date()
        if today != self.day:
            log.info("New trading day %s - risk state reset", today)
            self.day, self.daily_pnl, self.consecutive_losses = today, 0.0, 0
            self.day_start_equity, self.session_trades, self.halted_reason = None, {}, ""
            self.peak_equity, self.current_equity = None, None
            self.state, self.cooldown_until, self.reduced_since = RiskState.NORMAL, None, None
            self.wins_since_reduced, self.recent_pnls, self._last_tier = 0, [], 0

    def start_day(self, equity: float) -> None:
        """Called every tick: sets day-start equity once, tracks peak equity and expiry of timers."""
        self._roll_day()
        if self.day_start_equity is None:
            self.day_start_equity = equity
            self.peak_equity = equity
        self.update_equity(equity)

    def update_equity(self, equity: float) -> None:
        """Track peak equity (incl. floating P&L) and apply drawdown protection."""
        self._roll_day()
        self.current_equity = equity
        if self.peak_equity is None or equity > self.peak_equity:
            self.peak_equity = equity
        self._check_cooldown_expiry()
        if (self.state == RiskState.NORMAL and self.drawdown_pct() >= self.cfg.drawdown_protection_pct - 1e-9):
            self._enter_reduced(f"equity {self.drawdown_pct():.2f}% below daily peak")
        elif self.state == RiskState.REDUCED and self._recovered():
            self.state, self.reduced_since = RiskState.NORMAL, None
            log.info("Risk state: REDUCED -> NORMAL (recovery complete)")

    def drawdown_pct(self) -> float:
        if not self.peak_equity or self.current_equity is None:
            return 0.0
        return max(0.0, (self.peak_equity - self.current_equity) / self.peak_equity * 100)

    def growth_pct(self) -> float:
        """Realized daily growth vs day-start equity (floating P&L excluded)."""
        if not self.day_start_equity:
            return 0.0
        return self.daily_pnl / self.day_start_equity * 100

    def _enter_reduced(self, why: str) -> None:
        self.state, self.reduced_since, self.wins_since_reduced = RiskState.REDUCED, utc_now(), 0
        log.warning("Risk state -> REDUCED (%.2f%% risk): %s", self.cfg.reduced_risk_pct, why)

    def _recovered(self) -> bool:
        if self.reduced_since is None:
            return True
        waited = utc_now() - self.reduced_since >= timedelta(minutes=self.cfg.recovery_minutes)
        return (waited and self.wins_since_reduced >= self.cfg.recovery_wins
                and self.drawdown_pct() < self.cfg.drawdown_protection_pct)

    def _start_cooldown(self) -> None:
        self.state = RiskState.COOLDOWN
        self.cooldown_until = utc_now() + timedelta(minutes=self.cfg.cooldown_minutes)
        log.warning("Risk state -> COOLDOWN: %d consecutive losses, no new entries until %s UTC",
                    self.consecutive_losses, self.cooldown_until.strftime("%H:%M:%S"))

    def _check_cooldown_expiry(self) -> None:
        if self.state == RiskState.COOLDOWN and self.cooldown_until and utc_now() >= self.cooldown_until:
            self.cooldown_until, self.consecutive_losses = None, 0
            self._enter_reduced("cooldown expired - automatic resumption")
            log.info("Cooldown over: trading resumes automatically at %.2f%% risk", self.cfg.reduced_risk_pct)

    def record_trade(self, pnl: float) -> None:
        """Record a closed trade's net P&L (broker P&L already includes commission/swap/spread)."""
        self._roll_day()
        self.daily_pnl += pnl
        self.recent_pnls = (self.recent_pnls + [pnl])[-max(1, self.cfg.perf_window_trades):]
        self.consecutive_losses = self.consecutive_losses + 1 if pnl <= 0 else 0
        if self.state == RiskState.REDUCED and pnl > 0:
            self.wins_since_reduced += 1
        log.info("Risk state: daily P&L %.2f (%.2f%%), consecutive losses %d, mode %s",
                 self.daily_pnl, self.growth_pct(), self.consecutive_losses, self.state.value)
        if self.consecutive_losses >= self.cfg.max_consecutive_losses and self.state != RiskState.COOLDOWN:
            self._start_cooldown()
        tier = self._tier_index()
        if tier != self._last_tier:
            log.info("Equity milestone: growth %.2f%% -> risk tier %d", self.growth_pct(), tier)
            self._last_tier = tier

    def record_entry(self, session_key: str) -> None:
        self.session_trades[session_key] = self.session_trades.get(session_key, 0) + 1

    def register_open(self, pid: str, risk_pct: float) -> None:
        self.open_risk[pid] = risk_pct

    def release(self, pid: str) -> None:
        self.open_risk.pop(pid, None)

    def aggregate_open_risk_pct(self) -> float:
        return sum(self.open_risk.values())

    # ---- adaptive risk ---------------------------------------------------------
    def _performance_ok(self) -> bool:
        """Gates for escalating risk: win rate > min, positive expectancy, acceptable drawdown."""
        if len(self.recent_pnls) < self.cfg.perf_min_trades:
            return False
        wins = sum(1 for x in self.recent_pnls if x > 0)
        return (wins / len(self.recent_pnls) > self.cfg.perf_min_win_rate
                and sum(self.recent_pnls) / len(self.recent_pnls) > 0
                and self.drawdown_pct() < self.cfg.drawdown_protection_pct / 2)

    def _tier_index(self) -> int:
        g = self.growth_pct()
        if g >= self.cfg.risk_tier2_growth_pct:
            return 2
        return 1 if g >= self.cfg.risk_tier1_growth_pct else 0

    def current_risk_pct(self) -> float:
        """Risk per trade (% of equity) for the current mode / growth tier."""
        if not self.cfg.adaptive_risk_enabled:
            return self.cfg.risk_per_trade_pct
        if self.state == RiskState.REDUCED:
            return min(self.cfg.reduced_risk_pct, self.cfg.risk_per_trade_pct)
        tier = self._tier_index()
        pct = self.cfg.risk_per_trade_pct
        if tier and self._performance_ok():
            pct = self.cfg.risk_tier2_pct if tier == 2 else self.cfg.risk_tier1_pct
        return min(pct, self.cfg.max_risk_per_trade_pct)

    # ---- checks ----------------------------------------------------------------
    def daily_loss_limit(self) -> float:
        return (self.day_start_equity or 0.0) * self.cfg.daily_loss_limit_pct / 100

    def daily_loss_hit(self) -> bool:
        """Hard stop: realized loss or equity-based loss (incl. floating) beyond the limit."""
        if not self.day_start_equity:
            return False
        limit = self.daily_loss_limit()
        floating_total = (self.current_equity - self.day_start_equity) if self.current_equity is not None else 0.0
        return self.daily_pnl <= -limit or floating_total <= -limit

    def profit_target_hit(self) -> bool:
        return bool(self.day_start_equity) and self.growth_pct() >= self.cfg.daily_profit_target_pct

    def circuit_breaker_tripped(self) -> bool:
        self._roll_day()
        self._check_cooldown_expiry()
        reason = ""
        if self.daily_loss_hit():
            reason = f"Daily loss limit reached ({self.daily_pnl:.2f})"
        elif self.profit_target_hit():
            reason = f"Daily profit target reached ({self.growth_pct():.2f}%)"
        elif self.state == RiskState.COOLDOWN and self.cooldown_until:
            left = max(0, int((self.cooldown_until - utc_now()).total_seconds() // 60) + 1)
            reason = f"Cooldown after {self.cfg.max_consecutive_losses} consecutive losses ({left} min left)"
        if reason and reason != self.halted_reason:
            log.warning("Trading blocked: %s", reason)
        self.halted_reason = reason
        return bool(reason)

    def can_trade(self, session_key: str = "") -> Tuple[bool, str]:
        if self.circuit_breaker_tripped():
            return False, self.halted_reason
        if session_key and self.session_trades.get(session_key, 0) >= self.cfg.max_trades_per_session:
            return False, f"Max trades for {session_key} reached"
        if self.cfg.adaptive_risk_enabled and \
                self.aggregate_open_risk_pct() + 1e-9 >= self.cfg.max_aggregate_risk_pct:
            return False, f"Aggregate open risk cap reached ({self.aggregate_open_risk_pct():.2f}%)"
        return True, "ok"

    def check_rr(self, entry: float, sl: float, tp: float) -> bool:
        risk = abs(entry - sl)
        return risk > 0 and abs(tp - entry) / risk >= self.cfg.min_rr - 1e-9

    # ---- sizing ----------------------------------------------------------------
    def position_size(self, equity: float, entry: float, sl: float, spec: SymbolSpec) -> float:
        """Lots such that hitting SL loses ~current risk% of equity (scaled by size multiplier)."""
        sl_pips = abs(entry - sl) / spec.pip_size
        self.last_risk_pct = 0.0
        if equity <= 0 or sl_pips <= 0:
            return 0.0
        risk_pct = self.current_risk_pct() * self.cfg.position_size_multiplier
        if self.cfg.adaptive_risk_enabled:  # never exceed aggregate cap with overlapping positions
            risk_pct = min(risk_pct, self.cfg.max_aggregate_risk_pct - self.aggregate_open_risk_pct())
            if risk_pct <= 0:
                return 0.0
        risk_amt = equity * risk_pct / 100
        lots = risk_amt / (sl_pips * spec.pip_value_per_lot)
        lots = int(lots / spec.lot_step + 1e-9) * spec.lot_step  # round down to step
        lots = min(lots, spec.max_lot)
        if lots < spec.min_lot:
            log.warning("Computed lot %.4f below minimum %.2f - trade skipped (risk too small)",
                        lots, spec.min_lot)
            return 0.0
        self.last_risk_pct = lots * sl_pips * spec.pip_value_per_lot / equity * 100
        log.info("Risk calc: mode=%s equity=%.2f risk=%.2f%% (%.2f) sl_pips=%.1f -> %.2f lots",
                 self.state.value, equity, risk_pct, risk_amt, sl_pips, lots)
        return round(lots, 8)

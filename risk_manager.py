"""Position sizing, risk limits and circuit breakers."""
from __future__ import annotations

from datetime import date
from typing import Dict, Optional, Tuple

from brokers.base_connector import SymbolSpec
from config import Config
from utils.logger import get_logger
from utils.time_utils import utc_now

log = get_logger("risk")


class RiskManager:
    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self.day: date = utc_now().date()
        self.day_start_equity: Optional[float] = None
        self.daily_pnl = 0.0
        self.consecutive_losses = 0
        self.session_trades: Dict[str, int] = {}
        self.halted_reason = ""

    # ---- state -----------------------------------------------------------------
    def _roll_day(self) -> None:
        today = utc_now().date()
        if today != self.day:
            self.day, self.daily_pnl, self.consecutive_losses = today, 0.0, 0
            self.day_start_equity, self.session_trades, self.halted_reason = None, {}, ""

    def start_day(self, equity: float) -> None:
        self._roll_day()
        if self.day_start_equity is None:
            self.day_start_equity = equity

    def record_trade(self, pnl: float) -> None:
        self._roll_day()
        self.daily_pnl += pnl
        self.consecutive_losses = self.consecutive_losses + 1 if pnl <= 0 else 0
        log.info("Risk state: daily P&L %.2f, consecutive losses %d", self.daily_pnl, self.consecutive_losses)

    def record_entry(self, session_key: str) -> None:
        self.session_trades[session_key] = self.session_trades.get(session_key, 0) + 1

    # ---- checks ----------------------------------------------------------------
    def daily_loss_limit(self) -> float:
        return (self.day_start_equity or 0.0) * self.cfg.daily_loss_limit_pct / 100

    def circuit_breaker_tripped(self) -> bool:
        self._roll_day()
        if self.day_start_equity and self.daily_pnl <= -self.daily_loss_limit():
            self.halted_reason = f"Daily loss limit reached ({self.daily_pnl:.2f})"
        elif self.consecutive_losses >= self.cfg.max_consecutive_losses:
            self.halted_reason = f"{self.consecutive_losses} consecutive losses"
        else:
            self.halted_reason = ""
        return bool(self.halted_reason)

    def can_trade(self, session_key: str = "") -> Tuple[bool, str]:
        if self.circuit_breaker_tripped():
            return False, self.halted_reason
        if session_key and self.session_trades.get(session_key, 0) >= self.cfg.max_trades_per_session:
            return False, f"Max trades for {session_key} reached"
        return True, "ok"

    def check_rr(self, entry: float, sl: float, tp: float) -> bool:
        risk = abs(entry - sl)
        if risk <= 0:
            return False
        rr = abs(tp - entry) / risk
        if self.cfg.scalp_mode:
            return self.cfg.min_rr_scalp - 1e-9 <= rr <= self.cfg.max_rr_scalp + 1e-9
        return rr >= self.cfg.min_rr - 1e-9

    def split_lots(self, lots: float, spec: SymbolSpec) -> Tuple[float, float]:
        """Split a position into (first-TP leg, runner leg); runner is 0 if it can't be split."""
        ratio = self.cfg.partial_tp_ratio
        if not self.cfg.scalp_mode or not 0 < ratio < 1:
            return lots, 0.0
        first = int(lots * ratio / spec.lot_step + 1e-9) * spec.lot_step
        rest = round(lots - first, 8)
        if first < spec.min_lot or rest < spec.min_lot:
            return lots, 0.0
        return round(first, 8), rest

    # ---- sizing ----------------------------------------------------------------
    def position_size(self, equity: float, entry: float, sl: float, spec: SymbolSpec) -> float:
        """Lots such that hitting SL loses ~risk% of equity (scaled by size multiplier)."""
        sl_pips = abs(entry - sl) / spec.pip_size
        if equity <= 0 or sl_pips <= 0:
            return 0.0
        risk_amt = equity * self.cfg.risk_per_trade_pct / 100 * self.cfg.position_size_multiplier
        lots = risk_amt / (sl_pips * spec.pip_value_per_lot)
        lots = int(lots / spec.lot_step + 1e-9) * spec.lot_step  # round down to step
        lots = min(lots, spec.max_lot)
        if lots < spec.min_lot:
            log.warning("Computed lot %.4f below minimum %.2f - trade skipped (risk too small)",
                        lots, spec.min_lot)
            return 0.0
        log.info("Risk calc: equity=%.2f risk=%.2f sl_pips=%.1f -> %.2f lots", equity, risk_amt, sl_pips, lots)
        return round(lots, 8)

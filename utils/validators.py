"""Input validation helpers."""
from __future__ import annotations

import re

from config import Config
from utils.time_utils import TIMEFRAME_MINUTES

_SYMBOL_RE = re.compile(r"^[A-Z0-9._]{3,20}$")


def validate_symbol(symbol: str) -> str:
    s = symbol.strip().upper()
    if not _SYMBOL_RE.match(s):
        raise ValueError(f"Invalid symbol: {symbol!r}")
    return s


def validate_order(side: str, volume: float, sl: float, tp: float, entry: float) -> None:
    if side not in ("buy", "sell"):
        raise ValueError("side must be 'buy' or 'sell'")
    if volume <= 0:
        raise ValueError("volume must be positive")
    if side == "buy" and not (sl < entry < tp):
        raise ValueError("buy requires SL < entry < TP")
    if side == "sell" and not (tp < entry < sl):
        raise ValueError("sell requires TP < entry < SL")


def validate_config(cfg: Config) -> list:
    """Return a list of problems (empty = OK)."""
    errors = []
    if cfg.broker not in ("tradelocker", "mt5", "sim"):
        errors.append("BROKER must be tradelocker, mt5 or sim")
    if cfg.timeframe not in TIMEFRAME_MINUTES:
        errors.append(f"Unsupported TIMEFRAME {cfg.timeframe}")
    if not 0 < cfg.risk_per_trade_pct <= 10:
        errors.append("RISK_PER_TRADE_PCT must be in (0, 10]")
    if cfg.min_rr < 2.0:
        errors.append("MIN_RR must be >= 2.0")
    if cfg.broker == "tradelocker" and not cfg.simulation_mode and not (
            cfg.tl_email and cfg.tl_password and cfg.tl_server):
        errors.append("TradeLocker credentials missing (TL_EMAIL/TL_PASSWORD/TL_SERVER)")
    for s in cfg.symbols:
        try:
            validate_symbol(s)
        except ValueError as exc:
            errors.append(str(exc))
    return errors

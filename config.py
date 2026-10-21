"""Global configuration, loaded from environment / ``.env`` (never hardcode credentials)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List

BASE_DIR = Path(__file__).resolve().parent
ENV_PATH = BASE_DIR / ".env"
LOG_DIR = BASE_DIR / "logs"


def load_env(path: Path = ENV_PATH) -> None:
    """Minimal .env loader (existing environment variables win)."""
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.split(" #")[0].strip().strip('"').strip("'")
        os.environ.setdefault(key.strip(), value)


def _get(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _float(name: str, default: float) -> float:
    try:
        return float(_get(name, str(default)))
    except ValueError:
        return default


def _int(name: str, default: int) -> int:
    try:
        return int(float(_get(name, str(default))))
    except ValueError:
        return default


def _bool(name: str, default: bool) -> bool:
    return _get(name, str(default)).lower() in ("1", "true", "yes", "on")


def _list(name: str, default: str = "") -> List[str]:
    return [s.strip().upper() for s in _get(name, default).split(",") if s.strip()]


@dataclass
class Config:
    broker: str = "tradelocker"
    simulation_mode: bool = True
    # TradeLocker
    tl_environment: str = "demo"
    tl_email: str = ""
    tl_password: str = ""
    tl_server: str = ""
    tl_acc_num: int = 0
    # MT5
    mt5_login: int = 0
    mt5_password: str = ""
    mt5_server: str = ""
    # Trading
    symbols: List[str] = field(default_factory=lambda: ["EURUSD"])
    timeframe: str = "M5"
    htf_timeframe: str = "M15"
    enabled_sessions: List[str] = field(default_factory=lambda: ["LONDON", "NY", "ASIA"])
    session_hours: Dict[str, tuple] = field(default_factory=dict)
    session_symbols: Dict[str, List[str]] = field(default_factory=dict)
    # Risk
    risk_per_trade_pct: float = 1.0  # base (tier 0) risk per trade
    position_size_multiplier: float = 1.0
    min_rr: float = 2.0
    daily_loss_limit_pct: float = 3.0  # hard daily stop (% of day-start equity)
    max_consecutive_losses: int = 3  # losses before cooldown
    max_trades_per_session: int = 5
    breakeven_r: float = 1.0
    trailing_r: float = 1.5
    # Adaptive compounding / dynamic risk (see risk_manager.py)
    adaptive_risk_enabled: bool = True
    daily_profit_target_pct: float = 15.0  # stop opening new trades at/above this realized growth
    risk_tier1_growth_pct: float = 5.0  # growth at which tier-1 risk applies
    risk_tier1_pct: float = 1.25
    risk_tier2_growth_pct: float = 10.0
    risk_tier2_pct: float = 1.5
    max_risk_per_trade_pct: float = 1.5  # cap on normal-mode risk
    reduced_risk_pct: float = 0.5  # REDUCED mode / post-cooldown risk
    drawdown_protection_pct: float = 2.0  # drop from daily peak equity that triggers REDUCED
    max_aggregate_risk_pct: float = 3.0  # cap on summed risk of all open positions
    cooldown_minutes: int = 30
    recovery_minutes: int = 30  # min time in REDUCED before risk may escalate again
    recovery_wins: int = 2  # winning trades required in REDUCED before escalating
    perf_window_trades: int = 10  # recent-trade window for performance gates
    perf_min_trades: int = 3  # min closed trades today before risk may escalate
    perf_min_win_rate: float = 0.5  # win rate must exceed this to escalate
    # ICT
    swing_lookback: int = 3
    fvg_min_pips: float = 1.0
    ob_impulse_atr: float = 1.5
    equal_level_tolerance_pips: float = 2.0
    min_confluence: int = 2
    # Dashboard
    dashboard_host: str = "127.0.0.1"
    dashboard_port: int = 8080
    open_browser: bool = True

    @classmethod
    def from_env(cls) -> "Config":
        load_env()
        hours = {
            "LONDON": (_int("LONDON_START", 8), _int("LONDON_END", 16)),
            "NY": (_int("NY_START", 13), _int("NY_END", 21)),
            "ASIA": (_int("ASIA_START", 22), _int("ASIA_END", 8)),
        }
        symbols = _list("SYMBOLS", "EURUSD")
        session_symbols = {s: _list(f"SESSION_SYMBOLS_{s}") or list(symbols) for s in hours}
        return cls(
            broker=_get("BROKER", "tradelocker").lower(),
            simulation_mode=_bool("SIMULATION_MODE", True),
            tl_environment=_get("TL_ENVIRONMENT", "demo").lower(),
            tl_email=_get("TL_EMAIL"),
            tl_password=_get("TL_PASSWORD"),
            tl_server=_get("TL_SERVER"),
            tl_acc_num=_int("TL_ACC_NUM", 0),
            mt5_login=_int("MT5_LOGIN", 0),
            mt5_password=_get("MT5_PASSWORD"),
            mt5_server=_get("MT5_SERVER"),
            symbols=symbols,
            timeframe=_get("TIMEFRAME", "M5").upper(),
            htf_timeframe=_get("HTF_TIMEFRAME", "M15").upper(),
            enabled_sessions=_list("ENABLED_SESSIONS", "LONDON,NY,ASIA"),
            session_hours=hours,
            session_symbols=session_symbols,
            risk_per_trade_pct=_float("RISK_PER_TRADE_PCT", 1.0),
            position_size_multiplier=_float("POSITION_SIZE_MULTIPLIER", 1.0),
            min_rr=_float("MIN_RR", 2.0),
            daily_loss_limit_pct=_float("DAILY_LOSS_LIMIT_PCT", 3.0),
            max_consecutive_losses=_int("MAX_CONSECUTIVE_LOSSES", 3),
            max_trades_per_session=_int("MAX_TRADES_PER_SESSION", 5),
            breakeven_r=_float("BREAKEVEN_R", 1.0),
            trailing_r=_float("TRAILING_R", 1.5),
            adaptive_risk_enabled=_bool("ADAPTIVE_RISK_ENABLED", True),
            daily_profit_target_pct=_float("DAILY_PROFIT_TARGET_PCT", 15.0),
            risk_tier1_growth_pct=_float("RISK_TIER1_GROWTH_PCT", 5.0),
            risk_tier1_pct=_float("RISK_TIER1_PCT", 1.25),
            risk_tier2_growth_pct=_float("RISK_TIER2_GROWTH_PCT", 10.0),
            risk_tier2_pct=_float("RISK_TIER2_PCT", 1.5),
            max_risk_per_trade_pct=_float("MAX_RISK_PER_TRADE_PCT", 1.5),
            reduced_risk_pct=_float("REDUCED_RISK_PCT", 0.5),
            drawdown_protection_pct=_float("DRAWDOWN_PROTECTION_PCT", 2.0),
            max_aggregate_risk_pct=_float("MAX_AGGREGATE_RISK_PCT", 3.0),
            cooldown_minutes=_int("COOLDOWN_MINUTES", 30),
            recovery_minutes=_int("RECOVERY_MINUTES", 30),
            recovery_wins=_int("RECOVERY_WINS", 2),
            perf_window_trades=_int("PERF_WINDOW_TRADES", 10),
            perf_min_trades=_int("PERF_MIN_TRADES", 3),
            perf_min_win_rate=_float("PERF_MIN_WIN_RATE", 0.5),
            swing_lookback=_int("SWING_LOOKBACK", 3),
            fvg_min_pips=_float("FVG_MIN_PIPS", 1.0),
            ob_impulse_atr=_float("OB_IMPULSE_ATR", 1.5),
            equal_level_tolerance_pips=_float("EQUAL_LEVEL_TOLERANCE_PIPS", 2.0),
            min_confluence=_int("MIN_CONFLUENCE", 2),
            dashboard_host=_get("DASHBOARD_HOST", "127.0.0.1"),
            dashboard_port=_int("DASHBOARD_PORT", 8080),
            open_browser=_bool("OPEN_BROWSER", True),
        )

    def __post_init__(self) -> None:
        if not self.session_hours:
            self.session_hours = {"LONDON": (8, 16), "NY": (13, 21), "ASIA": (22, 8)}
        if not self.session_symbols:
            self.session_symbols = {s: list(self.symbols) for s in self.session_hours}

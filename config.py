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
    risk_per_trade_pct: float = 10.0
    position_size_multiplier: float = 1.0
    min_rr: float = 2.0
    daily_loss_limit_pct: float = 5.0
    max_consecutive_losses: int = 3
    max_trades_per_session: int = 5
    breakeven_r: float = 1.0
    trailing_r: float = 1.5
    # ICT
    swing_lookback: int = 3
    fvg_min_pips: float = 1.0
    ob_impulse_atr: float = 1.5
    equal_level_tolerance_pips: float = 2.0
    min_confluence: int = 2
    # Scalp mode (multi-timeframe confirmation; off by default = original behaviour)
    scalp_mode: bool = False
    min_confluence_scalp: int = 3
    enable_htf_confirmation: bool = True
    require_htf_structure: bool = True
    htf_lookback_candles: int = 50
    htf_min_body_ratio: float = 0.5
    recent_sweep_bars: int = 10
    min_entry_candle_strength: float = 0.70
    reject_indecision: bool = True
    min_rr_scalp: float = 1.5
    max_rr_scalp: float = 3.0
    max_sl_pips_scalp: float = 20.0
    prioritize_unmitigated_ob: bool = True
    recent_fvg_bars: int = 10
    deep_fvg_min_pips: float = 3.0
    min_atr_ratio: float = 0.50
    min_range_pips: float = 10.0
    require_close_confirmation: bool = True
    scalp_tp_pips: float = 12.0
    scalp_trail_momentum: bool = True
    partial_tp_ratio: float = 0.50
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
            risk_per_trade_pct=_float("RISK_PER_TRADE_PCT", 10.0),
            position_size_multiplier=_float("POSITION_SIZE_MULTIPLIER", 1.0),
            min_rr=_float("MIN_RR", 2.0),
            daily_loss_limit_pct=_float("DAILY_LOSS_LIMIT_PCT", 5.0),
            max_consecutive_losses=_int("MAX_CONSECUTIVE_LOSSES", 3),
            max_trades_per_session=_int("MAX_TRADES_PER_SESSION", 5),
            breakeven_r=_float("BREAKEVEN_R", 1.0),
            trailing_r=_float("TRAILING_R", 1.5),
            swing_lookback=_int("SWING_LOOKBACK", 3),
            fvg_min_pips=_float("FVG_MIN_PIPS", 1.0),
            ob_impulse_atr=_float("OB_IMPULSE_ATR", 1.5),
            equal_level_tolerance_pips=_float("EQUAL_LEVEL_TOLERANCE_PIPS", 2.0),
            min_confluence=_int("MIN_CONFLUENCE", 2),
            scalp_mode=_bool("SCALP_MODE", False),
            min_confluence_scalp=_int("MIN_CONFLUENCE_SCALP", 3),
            enable_htf_confirmation=_bool("ENABLE_HTF_CONFIRMATION", True),
            require_htf_structure=_bool("REQUIRE_HTF_STRUCTURE", True),
            htf_lookback_candles=_int("HTF_LOOKBACK_CANDLES", 50),
            htf_min_body_ratio=_float("HTF_MIN_BODY_RATIO", 0.5),
            recent_sweep_bars=_int("RECENT_SWEEP_BARS", 10),
            min_entry_candle_strength=_float("MIN_ENTRY_CANDLE_STRENGTH", 0.70),
            reject_indecision=_bool("REJECT_INDECISION", True),
            min_rr_scalp=_float("MIN_RR_SCALP", 1.5),
            max_rr_scalp=_float("MAX_RR_SCALP", 3.0),
            max_sl_pips_scalp=_float("MAX_SL_PIPS_SCALP", 20.0),
            prioritize_unmitigated_ob=_bool("PRIORITIZE_UNMITIGATED_OB", True),
            recent_fvg_bars=_int("RECENT_FVG_BARS", 10),
            deep_fvg_min_pips=_float("DEEP_FVG_MIN_PIPS", 3.0),
            min_atr_ratio=_float("MIN_ATR_RATIO", 0.50),
            min_range_pips=_float("MIN_RANGE_PIPS", 10.0),
            require_close_confirmation=_bool("REQUIRE_CLOSE_CONFIRMATION", True),
            scalp_tp_pips=_float("SCALP_TP_PIPS", 12.0),
            scalp_trail_momentum=_bool("SCALP_TRAIL_MOMENTUM", True),
            partial_tp_ratio=_float("PARTIAL_TP_RATIO", 0.50),
            dashboard_host=_get("DASHBOARD_HOST", "127.0.0.1"),
            dashboard_port=_int("DASHBOARD_PORT", 8080),
            open_browser=_bool("OPEN_BROWSER", True),
        )

    def __post_init__(self) -> None:
        if not self.session_hours:
            self.session_hours = {"LONDON": (8, 16), "NY": (13, 21), "ASIA": (22, 8)}
        if not self.session_symbols:
            self.session_symbols = {s: list(self.symbols) for s in self.session_hours}

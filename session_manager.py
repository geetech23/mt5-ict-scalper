"""Trading session detection and filtering (London / NY / Asia, all UTC)."""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

from config import Config
from utils.time_utils import ensure_utc, utc_now

DEFAULT_HOURS: Dict[str, Tuple[int, int]] = {"LONDON": (8, 16), "NY": (13, 21), "ASIA": (22, 8)}


class SessionManager:
    def __init__(self, hours: Optional[Dict[str, Tuple[int, int]]] = None,
                 enabled: Optional[List[str]] = None,
                 session_symbols: Optional[Dict[str, List[str]]] = None) -> None:
        self.hours = dict(hours or DEFAULT_HOURS)
        self.enabled = [s.upper() for s in (enabled if enabled is not None else self.hours)]
        self.session_symbols = {k.upper(): [s.upper() for s in v] for k, v in (session_symbols or {}).items()}

    @classmethod
    def from_config(cls, cfg: Config) -> "SessionManager":
        return cls(cfg.session_hours, cfg.enabled_sessions, cfg.session_symbols)

    @staticmethod
    def _in_window(hour_frac: float, start: int, end: int) -> bool:
        if start == end:
            return True
        if start < end:
            return start <= hour_frac < end
        return hour_frac >= start or hour_frac < end  # wraps midnight (Asia)

    def active_sessions(self, now: Optional[datetime] = None, enabled_only: bool = False) -> List[str]:
        now = ensure_utc(now or utc_now())
        hf = now.hour + now.minute / 60 + now.second / 3600
        names = self.enabled if enabled_only else list(self.hours)
        return [n for n in names if n in self.hours and self._in_window(hf, *self.hours[n])]

    def current_session(self, now: Optional[datetime] = None) -> str:
        """Display name of the active session(s), e.g. 'LONDON/NY' or 'NONE'."""
        active = self.active_sessions(now)
        return "/".join(active) if active else "NONE"

    def session_key(self, session: str, now: Optional[datetime] = None) -> str:
        """Identifier of one session occurrence (Asia started yesterday counts as yesterday)."""
        now = ensure_utc(now or utc_now())
        start, end = self.hours[session]
        day = now.date()
        if start > end and now.hour < end:
            day = day - timedelta(days=1)
        return f"{session}-{day.isoformat()}"

    def symbols_for(self, session: str) -> List[str]:
        return self.session_symbols.get(session, [])

    def tradable_sessions(self, symbol: str, now: Optional[datetime] = None) -> List[str]:
        symbol = symbol.upper()
        return [s for s in self.active_sessions(now, enabled_only=True)
                if not self.session_symbols or symbol in self.session_symbols.get(s, [symbol])]

    def is_trading_allowed(self, symbol: str, now: Optional[datetime] = None) -> bool:
        return bool(self.tradable_sessions(symbol, now))

    def next_change(self, now: Optional[datetime] = None) -> Tuple[datetime, str]:
        """Next moment any session opens/closes, with a description."""
        now = ensure_utc(now or utc_now())
        events = []
        for name, (start, end) in self.hours.items():
            for day in (0, 1):
                base = (now + timedelta(days=day)).replace(minute=0, second=0, microsecond=0)
                for h, what in ((start, "opens"), (end, "closes")):
                    t = base.replace(hour=h)
                    if t > now:
                        events.append((t, f"{name} {what}"))
        return min(events, key=lambda e: e[0])

    def seconds_to_change(self, now: Optional[datetime] = None) -> float:
        now = ensure_utc(now or utc_now())
        return (self.next_change(now)[0] - now).total_seconds()

    def status(self, now: Optional[datetime] = None) -> Dict[str, object]:
        now = ensure_utc(now or utc_now())
        when, what = self.next_change(now)
        return {"current": self.current_session(now), "active": self.active_sessions(now),
                "enabled": self.enabled, "next_change": what,
                "next_change_at": when.isoformat(),
                "seconds_to_change": int((when - now).total_seconds()),
                "hours": {k: list(v) for k, v in self.hours.items()}}

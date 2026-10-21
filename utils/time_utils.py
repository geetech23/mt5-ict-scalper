"""Time helpers (all times UTC)."""
from __future__ import annotations

from datetime import datetime, timezone

TIMEFRAME_MINUTES = {"M1": 1, "M5": 5, "M15": 15, "M30": 30, "H1": 60, "H4": 240, "D1": 1440}


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def ensure_utc(dt: datetime) -> datetime:
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


def timeframe_minutes(tf: str) -> int:
    try:
        return TIMEFRAME_MINUTES[tf.upper()]
    except KeyError as exc:
        raise ValueError(f"Unsupported timeframe: {tf}") from exc


def format_countdown(seconds: float) -> str:
    seconds = max(0, int(seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"

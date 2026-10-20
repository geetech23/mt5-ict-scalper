"""Trade journal: CSV of every event + recent-trade queries for the dashboard."""
from __future__ import annotations

import csv
import threading
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional

from config import LOG_DIR
from utils.logger import get_logger
from utils.time_utils import utc_now

log = get_logger("journal")
FIELDS = ["time", "event", "symbol", "side", "entry", "sl", "tp", "exit", "pnl", "reason"]


class Journal:
    def __init__(self, path: Optional[Path] = None) -> None:
        self.path = Path(path or LOG_DIR / "trade_journal.csv")
        self._lock = threading.Lock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            with self.path.open("w", newline="", encoding="utf-8") as f:
                csv.DictWriter(f, FIELDS).writeheader()

    def record(self, event: str, symbol: str = "", side: str = "", entry: Any = "", sl: Any = "",
               tp: Any = "", exit: Any = "", pnl: Any = "", reason: str = "") -> None:
        row = {"time": utc_now().isoformat(timespec="seconds"), "event": event, "symbol": symbol,
               "side": side, "entry": entry, "sl": sl, "tp": tp, "exit": exit, "pnl": pnl,
               "reason": reason.replace("\n", " ")}
        with self._lock, self.path.open("a", newline="", encoding="utf-8") as f:
            csv.DictWriter(f, FIELDS).writerow(row)

    def read(self) -> List[Dict[str, str]]:
        with self._lock, self.path.open(newline="", encoding="utf-8") as f:
            return list(csv.DictReader(f))

    def recent_trades(self, n: int = 20) -> List[Dict[str, str]]:
        return [r for r in self.read() if r["event"] == "CLOSE"][-n:][::-1]

    def daily_stats(self, day: Optional[date] = None) -> Dict[str, Any]:
        day_s = (day or utc_now().date()).isoformat()
        pnls = [float(r["pnl"]) for r in self.read()
                if r["event"] == "CLOSE" and r["time"].startswith(day_s) and r["pnl"] != ""]
        wins = sum(p > 0 for p in pnls)
        losses = sum(p <= 0 for p in pnls)
        return {"wins": wins, "losses": losses, "net_pnl": round(sum(pnls), 2),
                "win_rate": round(100 * wins / len(pnls), 1) if pnls else 0.0}

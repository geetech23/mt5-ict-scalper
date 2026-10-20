"""Centralized logging: colored console + rotating file."""
from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler

from config import LOG_DIR

_COLORS = {"DEBUG": "\033[36m", "INFO": "\033[32m", "WARNING": "\033[33m",
           "ERROR": "\033[31m", "CRITICAL": "\033[35m"}
_RESET = "\033[0m"
_configured = False


class ColorFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        msg = super().format(record)
        return f"{_COLORS.get(record.levelname, '')}{msg}{_RESET}"


def setup_logging(level: int = logging.INFO) -> None:
    global _configured
    if _configured:
        return
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    fmt = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
    root = logging.getLogger()
    root.setLevel(level)
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(ColorFormatter(fmt, "%H:%M:%S") if sys.stdout.isatty()
                         else logging.Formatter(fmt, "%H:%M:%S"))
    fileh = RotatingFileHandler(LOG_DIR / "bot.log", maxBytes=2_000_000, backupCount=5,
                                encoding="utf-8")
    fileh.setFormatter(logging.Formatter(fmt))
    root.addHandler(console)
    root.addHandler(fileh)
    logging.getLogger("werkzeug").setLevel(logging.WARNING)
    _configured = True


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)

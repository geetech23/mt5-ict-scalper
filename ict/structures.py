"""Data classes for ICT / Smart Money Concepts."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional


class Direction(str, Enum):
    BULLISH = "bullish"
    BEARISH = "bearish"
    NEUTRAL = "neutral"


class BreakType(str, Enum):
    BOS = "BOS"
    CHOCH = "CHoCH"


@dataclass
class Candle:
    time: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0

    @property
    def body(self) -> float:
        return abs(self.close - self.open)

    @property
    def is_bullish(self) -> bool:
        return self.close > self.open

    @property
    def is_bearish(self) -> bool:
        return self.close < self.open


@dataclass
class SwingPoint:
    index: int
    price: float
    is_high: bool
    label: str = ""  # HH, HL, LH, LL (or "" for the first of each kind)


@dataclass
class OrderBlock:
    index: int
    direction: Direction
    high: float
    low: float
    time: datetime
    mitigated: bool = False

    def contains(self, price: float) -> bool:
        return self.low <= price <= self.high


@dataclass
class FairValueGap:
    index: int  # index of the middle candle
    direction: Direction
    top: float
    bottom: float
    time: datetime
    filled: bool = False

    @property
    def size(self) -> float:
        return self.top - self.bottom

    def contains(self, price: float) -> bool:
        return self.bottom <= price <= self.top


@dataclass
class StructureBreak:
    index: int
    kind: BreakType
    direction: Direction
    level: float
    time: datetime


@dataclass
class LiquiditySweep:
    index: int
    direction: Direction  # direction of the expected reversal
    level: float
    time: datetime


@dataclass
class EqualLevel:
    price: float
    is_high: bool
    indices: List[int]


@dataclass
class ICTAnalysis:
    symbol: str = ""
    trend: Direction = Direction.NEUTRAL
    swings: List[SwingPoint] = field(default_factory=list)
    order_blocks: List[OrderBlock] = field(default_factory=list)
    fvgs: List[FairValueGap] = field(default_factory=list)
    breaks: List[StructureBreak] = field(default_factory=list)
    sweeps: List[LiquiditySweep] = field(default_factory=list)
    equal_levels: List[EqualLevel] = field(default_factory=list)
    last_index: int = -1

    def to_dict(self) -> Dict[str, Any]:
        def clean(o: Any) -> Any:
            if isinstance(o, dict):
                return {k: clean(v) for k, v in o.items()}
            if isinstance(o, (list, tuple)):
                return [clean(v) for v in o]
            if isinstance(o, Enum):
                return o.value
            if isinstance(o, datetime):
                return o.isoformat()
            return o
        return clean(asdict(self))


@dataclass
class Signal:
    symbol: str
    side: str  # "buy" | "sell"
    entry: float
    sl: float
    tp: float
    confluence: int
    reasons: List[str]
    time: Optional[datetime] = None
    tp1: Optional[float] = None  # first (partial) take-profit, scalp mode only
    score: float = 0.0

    @property
    def risk(self) -> float:
        return abs(self.entry - self.sl)

    @property
    def rr(self) -> float:
        return abs(self.tp - self.entry) / self.risk if self.risk else 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {"symbol": self.symbol, "side": self.side, "entry": self.entry, "sl": self.sl,
                "tp": self.tp, "rr": round(self.rr, 2), "confluence": self.confluence,
                "reasons": self.reasons, "tp1": self.tp1, "score": round(self.score, 2), "time": self.time.isoformat() if self.time else None}

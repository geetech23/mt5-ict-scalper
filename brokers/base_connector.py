"""Abstract broker interface. Implement this to add a new broker."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import List, Optional, Tuple

from ict.structures import Candle


@dataclass
class SymbolSpec:
    pip_size: float = 0.0001
    pip_value_per_lot: float = 10.0  # account-currency value of 1 pip for 1 lot
    min_lot: float = 0.01
    lot_step: float = 0.01
    max_lot: float = 100.0


def default_spec(symbol: str) -> SymbolSpec:
    """Reasonable fallback specs when the broker does not provide them."""
    s = symbol.upper()
    if s.startswith("XAU"):
        return SymbolSpec(pip_size=0.1, pip_value_per_lot=10.0)
    if s.startswith("XAG"):
        return SymbolSpec(pip_size=0.01, pip_value_per_lot=50.0)
    if s.endswith("JPY"):
        return SymbolSpec(pip_size=0.01, pip_value_per_lot=7.0)
    return SymbolSpec()


@dataclass
class AccountInfo:
    balance: float
    equity: float
    currency: str = "USD"


@dataclass
class Position:
    id: str
    symbol: str
    side: str  # "buy" | "sell"
    volume: float
    entry: float
    sl: float
    tp: float
    profit: float = 0.0
    current_price: float = 0.0


class BrokerError(Exception):
    """Raised for connector failures."""


class BaseConnector(ABC):
    name = "base"

    @abstractmethod
    def connect(self) -> bool: ...

    @abstractmethod
    def disconnect(self) -> None: ...

    @abstractmethod
    def is_connected(self) -> bool: ...

    @abstractmethod
    def get_account(self) -> AccountInfo: ...

    @abstractmethod
    def get_candles(self, symbol: str, timeframe: str, count: int = 300) -> List[Candle]: ...

    @abstractmethod
    def get_price(self, symbol: str) -> Tuple[float, float]:
        """Return (bid, ask)."""

    @abstractmethod
    def get_symbol_spec(self, symbol: str) -> SymbolSpec: ...

    @abstractmethod
    def place_order(self, symbol: str, side: str, volume: float, sl: float, tp: float) -> Optional[str]:
        """Market order. Returns an order/position id or None on failure."""

    @abstractmethod
    def get_positions(self) -> List[Position]: ...

    @abstractmethod
    def modify_position(self, position_id: str, sl: Optional[float] = None, tp: Optional[float] = None) -> bool: ...

    @abstractmethod
    def close_position(self, position_id: str) -> bool: ...

    def reconnect(self, attempts: int = 3, delay: float = 2.0) -> bool:
        import time
        for i in range(attempts):
            try:
                self.disconnect()
                if self.connect():
                    return True
            except Exception:  # noqa: BLE001 - keep retrying on any connector failure
                pass
            time.sleep(delay * (i + 1))
        return False

    def closed_result(self, position_id: str) -> Optional[Tuple[float, float]]:
        """(exit_price, pnl) of a position that disappeared, if the broker can tell."""
        return None

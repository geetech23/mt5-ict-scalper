"""Paper-trading connector. Uses synthetic prices, or real prices from `data_source`."""
from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

from brokers.base_connector import (AccountInfo, BaseConnector, Position, SymbolSpec, default_spec)
from ict.structures import Candle
from utils.logger import get_logger
from utils.time_utils import timeframe_minutes

log = get_logger("sim")


class SimulatedConnector(BaseConnector):
    name = "sim"

    def __init__(self, balance: float = 10_000.0, data_source: Optional[BaseConnector] = None,
                 seed: Optional[int] = None) -> None:
        self.balance = balance
        self.data_source = data_source
        self.rng = random.Random(seed)
        self._connected = False
        self._live: Dict[str, float] = {}
        self._positions: Dict[str, Position] = {}
        self._closed: Dict[str, Tuple[float, float]] = {}
        self._next_id = 1

    def connect(self) -> bool:
        if self.data_source and not self.data_source.connect():
            return False
        self._connected = True
        log.info("✅ Simulation mode active (no real orders will be sent)")
        return True

    def disconnect(self) -> None:
        if self.data_source:
            self.data_source.disconnect()
        self._connected = False

    def is_connected(self) -> bool:
        return self._connected

    # ---- prices ----------------------------------------------------------------
    @staticmethod
    def _base_price(symbol: str) -> float:
        s = symbol.upper()
        return 2000.0 if s.startswith("XAU") else 150.0 if s.endswith("JPY") else 1.1

    def get_candles(self, symbol: str, timeframe: str, count: int = 300) -> List[Candle]:
        if self.data_source:
            return self.data_source.get_candles(symbol, timeframe, count)
        spec = default_spec(symbol)
        rng = random.Random(hash((symbol, timeframe)) & 0xFFFF)
        minutes = timeframe_minutes(timeframe)
        now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
        now -= timedelta(minutes=now.minute % minutes)
        price, drift, candles = self._base_price(symbol), 0.0, []
        for i in range(count):
            if i % 25 == 0:
                drift = rng.uniform(-3, 3) * spec.pip_size
            o = price
            c = o + drift + rng.gauss(0, 4 * spec.pip_size)
            h = max(o, c) + abs(rng.gauss(0, 2 * spec.pip_size))
            l = min(o, c) - abs(rng.gauss(0, 2 * spec.pip_size))
            candles.append(Candle(now - timedelta(minutes=minutes * (count - i)), o, h, l, c, 100))
            price = c
        live = self._live.setdefault(symbol, price)
        live += self.rng.gauss(0, 1.5 * spec.pip_size)
        self._live[symbol] = live
        last = candles[-1]
        candles[-1] = Candle(last.time, last.open, max(last.high, live), min(last.low, live), live, last.volume)
        return candles

    def get_price(self, symbol: str) -> Tuple[float, float]:
        if self.data_source:
            return self.data_source.get_price(symbol)
        spec = default_spec(symbol)
        mid = self._live.setdefault(symbol, self._base_price(symbol))
        mid += self.rng.gauss(0, 1.0 * spec.pip_size)
        self._live[symbol] = mid
        half = spec.pip_size * 0.5
        return mid - half, mid + half

    def get_symbol_spec(self, symbol: str) -> SymbolSpec:
        return self.data_source.get_symbol_spec(symbol) if self.data_source else default_spec(symbol)

    # ---- account / orders --------------------------------------------------------
    def _pnl(self, p: Position, price: float) -> float:
        spec = self.get_symbol_spec(p.symbol)
        diff = (price - p.entry) if p.side == "buy" else (p.entry - price)
        return diff / spec.pip_size * spec.pip_value_per_lot * p.volume

    def get_account(self) -> AccountInfo:
        self.get_positions()
        return AccountInfo(self.balance, self.balance + sum(p.profit for p in self._positions.values()))

    def place_order(self, symbol: str, side: str, volume: float, sl: float, tp: float) -> Optional[str]:
        bid, ask = self.get_price(symbol)
        pid = f"SIM{self._next_id}"
        self._next_id += 1
        self._positions[pid] = Position(pid, symbol, side, volume, ask if side == "buy" else bid, sl, tp)
        log.info("🧪 [SIM] %s %s %.2f lots @ %.5f SL %.5f TP %.5f", side.upper(), symbol, volume,
                 self._positions[pid].entry, sl, tp)
        return pid

    def _settle(self, p: Position, price: float) -> None:
        pnl = self._pnl(p, price)
        self.balance += pnl
        self._closed[p.id] = (price, pnl)
        del self._positions[p.id]

    def get_positions(self) -> List[Position]:
        for p in list(self._positions.values()):
            bid, ask = self.get_price(p.symbol)
            price = bid if p.side == "buy" else ask
            if p.side == "buy":
                hit = p.sl if price <= p.sl else p.tp if price >= p.tp else None
            else:
                hit = p.sl if price >= p.sl else p.tp if price <= p.tp else None
            if hit is not None:
                self._settle(p, hit)
            else:
                p.current_price, p.profit = price, self._pnl(p, price)
        return list(self._positions.values())

    def modify_position(self, position_id: str, sl: Optional[float] = None, tp: Optional[float] = None) -> bool:
        p = self._positions.get(position_id)
        if not p:
            return False
        p.sl = p.sl if sl is None else sl
        p.tp = p.tp if tp is None else tp
        return True

    def close_position(self, position_id: str) -> bool:
        p = self._positions.get(position_id)
        if not p:
            return False
        bid, ask = self.get_price(p.symbol)
        self._settle(p, bid if p.side == "buy" else ask)
        return True

    def closed_result(self, position_id: str) -> Optional[Tuple[float, float]]:
        return self._closed.pop(position_id, None)

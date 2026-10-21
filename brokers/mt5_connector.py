"""MetaTrader 5 connector (optional; requires the Windows-only `MetaTrader5` package)."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, List, Optional, Tuple

from brokers.base_connector import (AccountInfo, BaseConnector, BrokerError, Position,
                                    SymbolSpec, default_spec)
from config import Config
from ict.structures import Candle
from utils.logger import get_logger

log = get_logger("mt5")


class MT5Connector(BaseConnector):
    name = "mt5"

    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self.mt5: Any = None
        self._connected = False

    def connect(self) -> bool:
        try:
            import MetaTrader5 as mt5  # type: ignore
        except ImportError as exc:
            raise BrokerError("MetaTrader5 package not installed (Windows only: pip install MetaTrader5)") from exc
        self.mt5 = mt5
        if not mt5.initialize(login=self.cfg.mt5_login, password=self.cfg.mt5_password,
                              server=self.cfg.mt5_server):
            log.error("❌ MT5 initialize failed: %s", mt5.last_error())
            return False
        self._connected = True
        log.info("✅ Connected to MT5")
        return True

    def disconnect(self) -> None:
        if self.mt5:
            self.mt5.shutdown()
        self._connected = False

    def is_connected(self) -> bool:
        return self._connected

    def get_account(self) -> AccountInfo:
        a = self.mt5.account_info()
        if a is None:
            raise BrokerError("account_info unavailable")
        return AccountInfo(float(a.balance), float(a.equity), a.currency)

    def get_candles(self, symbol: str, timeframe: str, count: int = 300) -> List[Candle]:
        tf = getattr(self.mt5, f"TIMEFRAME_{timeframe.upper()}")
        rates = self.mt5.copy_rates_from_pos(symbol, tf, 0, count)
        if rates is None:
            raise BrokerError(f"No rates for {symbol}")
        return [Candle(datetime.fromtimestamp(int(r["time"]), tz=timezone.utc), float(r["open"]),
                       float(r["high"]), float(r["low"]), float(r["close"]),
                       float(r["tick_volume"])) for r in rates]

    def get_price(self, symbol: str) -> Tuple[float, float]:
        t = self.mt5.symbol_info_tick(symbol)
        if t is None:
            raise BrokerError(f"No tick for {symbol}")
        return float(t.bid), float(t.ask)

    def get_symbol_spec(self, symbol: str) -> SymbolSpec:
        info = self.mt5.symbol_info(symbol)
        if info is None:
            return default_spec(symbol)
        pip = info.point * (10 if info.digits in (3, 5) else 1)
        per_pip = info.trade_tick_value / info.trade_tick_size * pip if info.trade_tick_size else 10.0
        return SymbolSpec(pip, per_pip, info.volume_min, info.volume_step, info.volume_max)

    def place_order(self, symbol: str, side: str, volume: float, sl: float, tp: float) -> Optional[str]:
        bid, ask = self.get_price(symbol)
        m = self.mt5
        req = {"action": m.TRADE_ACTION_DEAL, "symbol": symbol, "volume": volume,
               "type": m.ORDER_TYPE_BUY if side == "buy" else m.ORDER_TYPE_SELL,
               "price": ask if side == "buy" else bid, "sl": sl, "tp": tp, "deviation": 20,
               "magic": 5150, "comment": "ict-scalper", "type_filling": m.ORDER_FILLING_IOC}
        res = m.order_send(req)
        if res is None or res.retcode != m.TRADE_RETCODE_DONE:
            log.error("❌ MT5 order failed: %s", res)
            return None
        return str(res.order)

    def get_positions(self) -> List[Position]:
        pos = self.mt5.positions_get() or []
        return [Position(str(p.ticket), p.symbol, "buy" if p.type == 0 else "sell", p.volume,
                         p.price_open, p.sl, p.tp, p.profit, p.price_current) for p in pos]

    def modify_position(self, position_id: str, sl: Optional[float] = None, tp: Optional[float] = None) -> bool:
        p = next((x for x in self.get_positions() if x.id == position_id), None)
        if p is None:
            return False
        res = self.mt5.order_send({"action": self.mt5.TRADE_ACTION_SLTP, "position": int(position_id),
                                   "symbol": p.symbol, "sl": sl if sl is not None else p.sl,
                                   "tp": tp if tp is not None else p.tp})
        return res is not None and res.retcode == self.mt5.TRADE_RETCODE_DONE

    def close_position(self, position_id: str) -> bool:
        p = next((x for x in self.get_positions() if x.id == position_id), None)
        if p is None:
            return False
        bid, ask = self.get_price(p.symbol)
        m = self.mt5
        res = m.order_send({"action": m.TRADE_ACTION_DEAL, "position": int(position_id), "symbol": p.symbol,
                            "volume": p.volume, "type": m.ORDER_TYPE_SELL if p.side == "buy" else m.ORDER_TYPE_BUY,
                            "price": bid if p.side == "buy" else ask, "deviation": 20,
                            "type_filling": m.ORDER_FILLING_IOC})
        return res is not None and res.retcode == m.TRADE_RETCODE_DONE

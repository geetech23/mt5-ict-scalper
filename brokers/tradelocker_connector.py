"""TradeLocker connector (uses the official `tradelocker` package)."""
from __future__ import annotations

import time
from typing import Any, Dict, List, Optional, Tuple

from brokers.base_connector import (AccountInfo, BaseConnector, BrokerError, Position,
                                    SymbolSpec, default_spec)
from config import Config
from ict.structures import Candle
from utils.logger import get_logger
from utils.time_utils import timeframe_minutes

from datetime import datetime, timezone

log = get_logger("tradelocker")
_RES = {"M1": "1m", "M5": "5m", "M15": "15m", "M30": "30m", "H1": "1H", "H4": "4H", "D1": "1D"}


class TradeLockerConnector(BaseConnector):
    name = "tradelocker"

    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self.api: Any = None
        self._ids: Dict[str, int] = {}  # instrument id cache
        self._connected = False

    # ---- connection ------------------------------------------------------------
    def connect(self) -> bool:
        try:
            from tradelocker import TLAPI
        except ImportError as exc:
            raise BrokerError("`tradelocker` package not installed (pip install tradelocker)") from exc
        try:
            self.api = TLAPI(environment=self._environment(), username=self.cfg.tl_email,
                             password=self.cfg.tl_password, server=self.cfg.tl_server,
                             acc_num=self.cfg.tl_acc_num, log_level="warning")
            self._connected = True
            log.info("✅ Connected to TradeLocker (%s)", self.cfg.tl_environment)
            return True
        except Exception as exc:  # noqa: BLE001
            self._connected = False
            log.error("❌ TradeLocker connection failed: %s", exc)
            return False

    def _environment(self) -> str:
        env = self.cfg.tl_environment
        if env not in ("demo", "live"):
            raise BrokerError("TL_ENVIRONMENT must be 'demo' or 'live'")
        return "https://demo.tradelocker.com" if env == "demo" else "https://live.tradelocker.com"

    def disconnect(self) -> None:
        self.api = None
        self._connected = False

    def is_connected(self) -> bool:
        return self._connected and self.api is not None

    def _call(self, fn, *args, **kwargs):
        """Run an API call, reconnecting once on failure."""
        if not self.is_connected() and not self.reconnect():
            raise BrokerError("TradeLocker not connected")
        try:
            return fn(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001
            log.warning("⚠️ TradeLocker call failed (%s); reconnecting", exc)
            if not self.reconnect():
                self._connected = False
                raise BrokerError(str(exc)) from exc
            raise BrokerError(f"call failed after reconnect: {exc}") from exc

    # ---- data ------------------------------------------------------------------
    def _instrument_id(self, symbol: str) -> int:
        if symbol not in self._ids:
            iid = self._call(lambda: self.api.get_instrument_id_from_symbol_name(symbol))
            if iid is None:
                raise BrokerError(f"Unknown symbol {symbol}")
            self._ids[symbol] = int(iid)
        return self._ids[symbol]

    def get_account(self) -> AccountInfo:
        st = self._call(lambda: self.api.get_account_state())
        balance = float(st.get("balance", 0.0))
        pnl = float(st.get("openNetPnL", st.get("openGrossPnL", 0.0)) or 0.0)
        return AccountInfo(balance, balance + pnl)

    def get_candles(self, symbol: str, timeframe: str, count: int = 300) -> List[Candle]:
        iid = self._instrument_id(symbol)
        minutes = timeframe_minutes(timeframe)
        end = int(time.time() * 1000)
        start = end - count * minutes * 60_000
        df = self._call(lambda: self.api.get_price_history(
            iid, resolution=_RES[timeframe.upper()], start_timestamp=start, end_timestamp=end))
        candles = []
        for row in df.to_dict("records"):
            t = row.get("t", row.get("time"))
            ts = datetime.fromtimestamp(float(t) / 1000, tz=timezone.utc) if t is not None else datetime.now(timezone.utc)
            candles.append(Candle(ts, float(row["o"]), float(row["h"]), float(row["l"]),
                                  float(row["c"]), float(row.get("v", 0) or 0)))
        return candles[-count:]

    def get_price(self, symbol: str) -> Tuple[float, float]:
        q = self._call(lambda: self.api.get_quotes(self._instrument_id(symbol)))
        return float(q["bp"]), float(q["ap"])

    def get_symbol_spec(self, symbol: str) -> SymbolSpec:
        return default_spec(symbol)

    # ---- trading ---------------------------------------------------------------
    def place_order(self, symbol: str, side: str, volume: float, sl: float, tp: float) -> Optional[str]:
        try:
            oid = self._call(lambda: self.api.create_order(
                self._instrument_id(symbol), quantity=volume, side=side, type_="market",
                stop_loss=sl, stop_loss_type="absolute", take_profit=tp, take_profit_type="absolute"))
        except BrokerError as exc:
            log.error("❌ Order failed: %s", exc)
            return None
        return str(oid) if oid else None

    def get_positions(self) -> List[Position]:
        df = self._call(lambda: self.api.get_all_positions())
        out: List[Position] = []
        for r in df.to_dict("records"):
            try:
                sym = self.api.get_symbol_name_from_instrument_id(int(r["tradableInstrumentId"]))
            except Exception:  # noqa: BLE001
                sym = str(r.get("tradableInstrumentId"))
            out.append(Position(str(r["id"]), sym, str(r["side"]), float(r["qty"]), float(r["avgPrice"]),
                                float(r.get("stopLoss") or 0.0), float(r.get("takeProfit") or 0.0),
                                float(r.get("unrealizedPl") or 0.0)))
        return out

    def modify_position(self, position_id: str, sl: Optional[float] = None, tp: Optional[float] = None) -> bool:
        params: Dict[str, Any] = {}
        if sl is not None:
            params["stopLoss"] = sl
        if tp is not None:
            params["takeProfit"] = tp
        try:
            return bool(self._call(lambda: self.api.modify_position(int(position_id), params)))
        except BrokerError as exc:
            log.error("❌ Modify failed: %s", exc)
            return False

    def close_position(self, position_id: str) -> bool:
        try:
            return bool(self._call(lambda: self.api.close_position(position_id=int(position_id))))
        except BrokerError as exc:
            log.error("❌ Close failed: %s", exc)
            return False

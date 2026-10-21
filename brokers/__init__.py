"""Broker connectors and factory."""
from __future__ import annotations

from brokers.base_connector import (AccountInfo, BaseConnector, BrokerError, Position, SymbolSpec)
from config import Config


def create_connector(cfg: Config) -> BaseConnector:
    """Build the connector selected in config (BROKER / SIMULATION_MODE)."""
    from brokers.simulated_connector import SimulatedConnector

    if cfg.broker == "sim":
        return SimulatedConnector()
    if cfg.broker == "tradelocker":
        from brokers.tradelocker_connector import TradeLockerConnector
        real = TradeLockerConnector(cfg)
        has_creds = bool(cfg.tl_email and cfg.tl_password and cfg.tl_server)
        if cfg.simulation_mode:
            return SimulatedConnector(data_source=real if has_creds else None)
        return real
    if cfg.broker == "mt5":
        from brokers.mt5_connector import MT5Connector
        real = MT5Connector(cfg)
        return SimulatedConnector(data_source=real) if cfg.simulation_mode and cfg.mt5_login else (
            SimulatedConnector() if cfg.simulation_mode else real)
    raise BrokerError(f"Unknown broker: {cfg.broker}")


__all__ = ["AccountInfo", "BaseConnector", "BrokerError", "Position", "SymbolSpec", "create_connector"]

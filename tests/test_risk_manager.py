from brokers.base_connector import SymbolSpec
from config import Config
from risk_manager import RiskManager


def mgr(**kw):
    return RiskManager(Config(**kw))


def test_position_size_risk_percent():
    rm = mgr(risk_per_trade_pct=1.0)
    lots = rm.position_size(10_000, 1.1000, 1.0950, SymbolSpec())  # 50 pips, $100 risk
    assert lots == 0.2


def test_multiplier_and_min_lot():
    assert mgr(position_size_multiplier=2).position_size(10_000, 1.1, 1.095, SymbolSpec()) == 0.4
    assert mgr().position_size(2, 1.1, 1.095, SymbolSpec()) == 0.0  # $2 account too small for 0.01 lot


def test_rr_enforced():
    rm = mgr(min_rr=2.0)
    assert rm.check_rr(1.1, 1.095, 1.11)
    assert not rm.check_rr(1.1, 1.095, 1.104)


def test_daily_loss_circuit_breaker():
    rm = mgr(daily_loss_limit_pct=5, max_consecutive_losses=99)
    rm.start_day(1000)
    rm.record_trade(-30)
    assert rm.can_trade()[0]
    rm.record_trade(-25)
    assert not rm.can_trade()[0]


def test_consecutive_losses_and_session_cap():
    rm = mgr(max_consecutive_losses=2, max_trades_per_session=1)
    rm.start_day(1000)
    rm.record_entry("LONDON-x")
    assert not rm.can_trade("LONDON-x")[0]
    assert rm.can_trade("NY-x")[0]
    rm.record_trade(-1)
    rm.record_trade(-1)
    assert not rm.can_trade()[0]
    rm.record_trade(5)

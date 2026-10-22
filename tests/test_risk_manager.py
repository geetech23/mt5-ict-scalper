from brokers.base_connector import SymbolSpec
from config import Config
import logging

from risk_manager import RiskManager, RiskMode


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


def test_consecutive_losses_warn_but_never_block(caplog):
    rm = mgr(max_consecutive_losses=2, max_trades_per_session=1, adaptive_risk=False)
    rm.start_day(1000)
    rm.record_entry("LONDON-x")
    assert not rm.can_trade("LONDON-x")[0]
    assert rm.can_trade("NY-x")[0]
    with caplog.at_level(logging.WARNING, logger="risk"):
        for _ in range(4):
            rm.record_trade(-1)
    assert rm.consecutive_losses == 4
    assert rm.can_trade()[0]
    assert "4 consecutive losses" in caplog.text
    rm.record_trade(5)
    assert rm.consecutive_losses == 0


def win(rm, n, amt=10):
    for _ in range(n):
        rm.record_trade(amt)
        rm.update_equity(rm.day_start_equity + rm.daily_pnl)


def test_risk_tiers_need_performance_gate():
    rm = mgr()
    rm.start_day(1000)
    rm.record_trade(60)  # +6% but single trade: gate (3 trades) not met
    rm.update_equity(1060)
    assert rm.current_risk_pct() == 1.0
    win(rm, 2, 1)
    assert rm.current_risk_pct() == 1.25
    win(rm, 1, 40)  # > +10%
    assert rm.current_risk_pct() == 1.5
    assert rm.mode == RiskMode.ELEVATED


def test_no_escalation_with_bad_win_rate():
    rm = mgr()
    rm.start_day(1000)
    for p in (-1, -1, -1, 80):  # +7.7% but 25% win rate
        rm.record_trade(p)
    rm.update_equity(1077)
    assert rm.current_risk_pct() == 1.0


def test_profit_target_stops_new_trades():
    rm = mgr()
    rm.start_day(1000)
    rm.record_trade(149)
    assert rm.can_trade()[0]
    rm.record_trade(1)
    assert not rm.can_trade()[0]


def test_hard_daily_loss_includes_floating():
    rm = mgr()
    rm.start_day(1000)
    rm.update_equity(969)  # -3.1% floating
    assert not rm.can_trade()[0]


def test_peak_drawdown_reduces_risk_and_recovery_required():
    rm = mgr()
    rm.start_day(1000)
    win(rm, 3, 20)  # peak 1060
    rm.update_equity(1038)  # 2.08% below peak
    assert rm.mode == RiskMode.REDUCED
    assert rm.current_risk_pct() == 0.5
    rm.update_equity(1060)  # equity back, but no closed-trade recovery yet
    rm.record_trade(5)
    assert rm.mode == RiskMode.REDUCED
    rm.record_trade(5)
    rm.record_trade(5)
    rm.update_equity(1075)
    assert rm.mode == RiskMode.NORMAL


def test_position_size_uses_adaptive_risk_and_buffer():
    rm = mgr()
    rm.start_day(10_000)
    rm.update_equity(9_790)  # -2.1% from peak -> reduced 0.5%
    assert rm.position_size(10_000, 1.1000, 1.0950, SymbolSpec()) == 0.1
    assert mgr(risk_cost_buffer_pips=50).position_size(10_000, 1.1, 1.095, SymbolSpec()) == 0.1


def test_aggregate_risk_cap():
    rm = mgr(max_aggregate_risk_pct=3.0)
    rm.register_open_risk("a", 200)
    assert rm.within_aggregate_cap(100, 10_000)
    assert not rm.within_aggregate_cap(101, 10_000)
    rm.release_open_risk("a")
    assert rm.within_aggregate_cap(300, 10_000)


def test_state_persistence_roundtrip(tmp_path):
    rm = mgr()
    rm.start_day(1000)
    rm.record_trade(-5)
    f = tmp_path / "s.json"
    rm.save_state(f)
    rm2 = mgr()
    assert rm2.load_state(f)
    assert rm2.day_start_equity == 1000 and rm2.daily_pnl == -5
    assert not mgr().load_state(tmp_path / "missing.json")

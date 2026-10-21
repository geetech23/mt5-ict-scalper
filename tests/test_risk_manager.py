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


# ---- adaptive compounding / state machine ----------------------------------------
from datetime import timedelta

import risk_manager
from risk_manager import RiskState
from utils.time_utils import utc_now


def clock(monkeypatch, start=None):
    t = {"now": start or utc_now()}
    monkeypatch.setattr(risk_manager, "utc_now", lambda: t["now"])
    return t


def test_risk_tiers_require_performance(monkeypatch):
    clock(monkeypatch)
    rm = mgr(max_consecutive_losses=99)
    rm.start_day(10_000)
    assert rm.current_risk_pct() == 1.0
    rm.record_trade(600)  # +6% but only one trade -> gate blocks escalation
    assert rm.current_risk_pct() == 1.0
    rm.record_trade(10)
    rm.record_trade(10)
    assert rm.current_risk_pct() == 1.25
    rm.record_trade(500)  # ~+11.3%
    assert rm.current_risk_pct() == 1.5


def test_no_escalation_on_poor_win_rate(monkeypatch):
    clock(monkeypatch)
    rm = mgr(max_consecutive_losses=99)
    rm.start_day(10_000)
    for p in (-10, -10, 1500):
        rm.record_trade(p)
    assert rm.growth_pct() > 10 and rm.current_risk_pct() == 1.0  # win rate 33%


def test_profit_target_stops_new_trades(monkeypatch):
    clock(monkeypatch)
    rm = mgr(max_consecutive_losses=99)
    rm.start_day(1000)
    rm.record_trade(150)
    ok, why = rm.can_trade()
    assert not ok and "profit target" in why


def test_drawdown_from_peak_reduces_risk_and_recovery(monkeypatch):
    t = clock(monkeypatch)
    rm = mgr()
    rm.start_day(1000)
    rm.update_equity(1100)
    rm.update_equity(1077)  # 2.09% below peak
    assert rm.state == RiskState.REDUCED and rm.current_risk_pct() == 0.5
    rm.update_equity(1100)
    rm.record_trade(5)  # a single win does not restore risk
    assert rm.state == RiskState.REDUCED
    rm.record_trade(5)
    rm.update_equity(1100)
    assert rm.state == RiskState.REDUCED  # recovery period not elapsed
    t["now"] += timedelta(minutes=31)
    rm.update_equity(1100)
    assert rm.state == RiskState.NORMAL


def test_cooldown_and_automatic_resumption(monkeypatch):
    t = clock(monkeypatch)
    rm = mgr()
    rm.start_day(10_000)
    for _ in range(3):
        rm.record_trade(-10)
    assert rm.state == RiskState.COOLDOWN
    ok, why = rm.can_trade()
    assert not ok and "Cooldown" in why
    t["now"] += timedelta(minutes=29)
    assert not rm.can_trade()[0]
    t["now"] += timedelta(minutes=2)
    assert rm.can_trade()[0]
    assert rm.state == RiskState.REDUCED and rm.current_risk_pct() == 0.5
    assert rm.consecutive_losses == 0


def test_hard_daily_loss_overrides_resumption(monkeypatch):
    t = clock(monkeypatch)
    rm = mgr()
    rm.start_day(1000)
    for _ in range(3):
        rm.record_trade(-12)  # -3.6%
    t["now"] += timedelta(minutes=31)
    ok, why = rm.can_trade()
    assert not ok and "Daily loss" in why


def test_floating_loss_triggers_hard_stop(monkeypatch):
    clock(monkeypatch)
    rm = mgr()
    rm.start_day(1000)
    rm.update_equity(969)
    assert "Daily loss" in rm.can_trade()[1]


def test_aggregate_risk_cap(monkeypatch):
    clock(monkeypatch)
    rm = mgr(max_aggregate_risk_pct=1.5)
    rm.start_day(10_000)
    spec = SymbolSpec()
    assert rm.position_size(10_000, 1.1, 1.095, spec) == 0.2
    rm.register_open("a", rm.last_risk_pct)
    lots = rm.position_size(10_000, 1.1, 1.095, spec)
    assert lots == 0.1  # only 0.5% headroom left
    rm.register_open("b", rm.last_risk_pct)
    assert not rm.can_trade()[0]
    rm.release("a")
    assert rm.can_trade()[0]


def test_day_rollover_resets_state(monkeypatch):
    t = clock(monkeypatch)
    rm = mgr()
    rm.start_day(1000)
    for _ in range(3):
        rm.record_trade(-1)
    t["now"] += timedelta(days=1)
    rm.start_day(1200)
    assert rm.state == RiskState.NORMAL and rm.day_start_equity == 1200 and rm.can_trade()[0]

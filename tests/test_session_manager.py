from datetime import datetime, timezone

from session_manager import SessionManager


def at(h, m=0, d=15):
    return datetime(2024, 1, d, h, m, tzinfo=timezone.utc)


def test_active_sessions():
    sm = SessionManager()
    assert sm.active_sessions(at(7)) == ["ASIA"]
    assert sm.active_sessions(at(8)) == ["LONDON"]
    assert sm.active_sessions(at(14)) == ["LONDON", "NY"]
    assert sm.active_sessions(at(21)) == []
    assert sm.active_sessions(at(23)) == ["ASIA"]
    assert sm.active_sessions(at(0)) == ["ASIA"]
    assert sm.current_session(at(14)) == "LONDON/NY"
    assert sm.current_session(at(21, 30)) == "NONE"


def test_enabled_filter_and_symbols():
    sm = SessionManager(enabled=["LONDON"], session_symbols={"LONDON": ["EURUSD"], "NY": ["EURUSD"], "ASIA": ["USDJPY"]})
    assert sm.is_trading_allowed("EURUSD", at(10))
    assert not sm.is_trading_allowed("USDJPY", at(10))
    assert not sm.is_trading_allowed("EURUSD", at(18))  # NY not enabled


def test_countdown():
    sm = SessionManager()
    assert sm.seconds_to_change(at(7, 30)) == 1800
    assert sm.next_change(at(7, 30))[1] in ("ASIA closes", "LONDON opens")
    assert sm.seconds_to_change(at(21, 30)) == 1800  # Asia opens at 22:00


def test_asia_session_key_spans_midnight():
    sm = SessionManager()
    assert sm.session_key("ASIA", at(23, d=14)) == sm.session_key("ASIA", at(3, d=15))

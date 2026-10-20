import random
from datetime import datetime, timedelta, timezone

from backtest import Backtester
from bot import TradingBot
from brokers.simulated_connector import SimulatedConnector
from config import Config
from ict.structures import Candle
from journal import Journal


def random_candles(n=800, seed=3):
    rng = random.Random(seed)
    t, p, out = datetime(2024, 1, 1, tzinfo=timezone.utc), 1.1, []
    for i in range(n):
        o, c = p, p + rng.gauss(0, 0.0006) + (0.0004 if (i // 60) % 2 else -0.0004)
        out.append(Candle(t + timedelta(minutes=5 * i), o, max(o, c) + abs(rng.gauss(0, 0.0003)),
                          min(o, c) - abs(rng.gauss(0, 0.0003)), c))
        p = c
    return out


def test_backtest_runs_and_summarizes():
    res = Backtester(Config()).run(random_candles(), "EURUSD")
    assert {"trades", "win_rate", "profit_factor", "max_drawdown_pct", "net_pnl"} <= set(res)
    assert res["wins"] + res["losses"] == res["trades"]


def test_walk_forward():
    res = Backtester(Config()).walk_forward(random_candles(), "EURUSD", windows=2)
    assert [r["window"] for r in res] == [1, 2]


def test_bot_tick_in_simulation(tmp_path):
    cfg = Config(symbols=["EURUSD"])
    bot = TradingBot(cfg, SimulatedConnector(seed=1), Journal(tmp_path / "j.csv"), poll_seconds=0.01)
    bot.conn.connect()
    bot.tick()
    snap = bot.snapshot()
    assert snap["connected"] and snap["account"]["balance"] == 10_000
    assert "session" in snap and "stats" in snap


def test_simulated_position_hits_tp():
    c = SimulatedConnector(seed=1)
    c.connect()
    bid, ask = c.get_price("EURUSD")
    pid = c.place_order("EURUSD", "buy", 0.1, ask - 0.0010, ask + 0.0000001)
    c.get_positions()
    assert c.closed_result(pid) is not None
    assert c.get_positions() == []

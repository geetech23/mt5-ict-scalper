from datetime import datetime, timedelta, timezone

from ict.analyzer import ICTAnalyzer
from ict.signals import SignalGenerator
from ict.structures import BreakType, Candle, Direction

T0 = datetime(2024, 1, 1, tzinfo=timezone.utc)


def mk(rows):
    return [Candle(T0 + timedelta(minutes=5 * i), o, h, l, c) for i, (o, h, l, c) in enumerate(rows)]


def flat(n, price=1.1000):
    return [(price, price + 0.0005, price - 0.0005, price)] * n


def test_swings_and_labels():
    prices = [1.10, 1.11, 1.12, 1.11, 1.10, 1.11, 1.13, 1.12, 1.10, 1.09, 1.10, 1.11, 1.12]
    candles = mk([(p, p + 0.001, p - 0.001, p) for p in prices])
    sw = ICTAnalyzer(swing_lookback=2).find_swings(candles)
    highs = [s for s in sw if s.is_high]
    assert [s.index for s in highs] == [2, 6]
    assert highs[1].label == "HH"
    assert any(not s.is_high for s in sw)


def test_fvg_detection_and_fill():
    rows = flat(3) + [(1.1000, 1.1005, 1.0995, 1.1002), (1.1002, 1.1030, 1.1001, 1.1028), (1.1028, 1.1040, 1.1015, 1.1035)]
    a = ICTAnalyzer(fvg_min_pips=1)
    fvgs = a.find_fvgs(mk(rows))
    assert any(g.direction == Direction.BULLISH and not g.filled for g in fvgs)
    rows.append((1.1035, 1.1036, 1.0900, 1.0910))
    assert any(g.filled for g in a.find_fvgs(mk(rows)))


def test_order_block_bullish():
    rows = flat(20) + [(1.1005, 1.1006, 1.0995, 1.0997), (1.0997, 1.1060, 1.0996, 1.1055)]
    obs = ICTAnalyzer().find_order_blocks(mk(rows + flat(2, 1.1055)))
    assert len(obs) == 1 and obs[0].direction == Direction.BULLISH and obs[0].index == 20


def test_bos_choch_and_sweep():
    up = [1.10, 1.11, 1.12, 1.11, 1.10, 1.11, 1.12, 1.13, 1.14, 1.15]
    down = [1.14, 1.13, 1.12, 1.11, 1.10, 1.09, 1.08, 1.07]
    rows = [(p, p + 0.0005, p - 0.0005, p) for p in up + down]
    a = ICTAnalyzer(swing_lookback=2)
    c = mk(rows)
    trend, breaks = a.analyze_structure(c, a.find_swings(c))
    assert breaks[0].direction == Direction.BULLISH
    assert any(b.kind == BreakType.CHOCH and b.direction == Direction.BEARISH for b in breaks)
    assert trend == Direction.BEARISH

    sweep_rows = [(p, p + 0.001, p - 0.001, p) for p in [1.10, 1.11, 1.12, 1.11, 1.10, 1.10, 1.10]]
    sweep_rows.append((1.10, 1.1230, 1.0990, 1.1000))  # wicks above 1.121 high, closes below
    c = mk(sweep_rows)
    sw = a.find_sweeps(c, a.find_swings(c))
    assert sw and sw[0].direction == Direction.BEARISH


def test_equal_highs():
    rows = [(p, p + 0.0003, p - 0.0003, p) for p in [1.10, 1.12, 1.10, 1.09, 1.10, 1.1201, 1.10, 1.09]]
    a = ICTAnalyzer(swing_lookback=1, equal_tolerance_pips=2)
    eq = a.find_equal_levels(a.find_swings(mk(rows)))
    assert any(e.is_high and len(e.indices) == 2 for e in eq)


def test_signal_enforces_min_rr_and_confluence():
    rows = flat(25)
    rows += [(1.1000, 1.1002, 1.0975, 1.0980), (1.0980, 1.1050, 1.0978, 1.1045), (1.1045, 1.1048, 1.1004, 1.1006)]
    c = mk(rows)
    a = ICTAnalyzer(swing_lookback=2)
    analysis = a.analyze(c, "EURUSD")
    sig = SignalGenerator(min_confluence=1, min_rr=2.0).generate(c, analysis)
    if sig:
        assert sig.rr >= 2.0
        assert (sig.side == "buy" and sig.sl < sig.entry < sig.tp) or (sig.side == "sell" and sig.tp < sig.entry < sig.sl)
    assert SignalGenerator(min_confluence=10).generate(c, analysis) is None

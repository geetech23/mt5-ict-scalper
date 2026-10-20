"""ICT / Smart Money Concepts analysis engine (pure Python, broker independent)."""
from __future__ import annotations

from typing import List, Optional, Sequence

from ict.structures import (BreakType, Candle, Direction, EqualLevel, FairValueGap,
                            ICTAnalysis, LiquiditySweep, OrderBlock, StructureBreak, SwingPoint)


class ICTAnalyzer:
    """Detects swings, structure, order blocks, FVGs, sweeps and equal highs/lows."""

    def __init__(self, pip_size: float = 0.0001, swing_lookback: int = 3,
                 fvg_min_pips: float = 1.0, ob_impulse_atr: float = 1.5,
                 equal_tolerance_pips: float = 2.0, atr_period: int = 14) -> None:
        if swing_lookback < 1:
            raise ValueError("swing_lookback must be >= 1")
        self.pip = pip_size
        self.lookback = swing_lookback
        self.fvg_min = fvg_min_pips * pip_size
        self.ob_impulse_atr = ob_impulse_atr
        self.equal_tol = equal_tolerance_pips * pip_size
        self.atr_period = atr_period

    # ---- helpers -------------------------------------------------------------------
    def atr(self, candles: Sequence[Candle], end: Optional[int] = None) -> float:
        end = len(candles) if end is None else end
        start = max(1, end - self.atr_period)
        trs = [max(c.high - c.low, abs(c.high - p.close), abs(c.low - p.close))
               for p, c in zip(candles[start - 1:end - 1], candles[start:end])]
        return sum(trs) / len(trs) if trs else 0.0

    # ---- swings / structure ----------------------------------------------------------
    def find_swings(self, candles: Sequence[Candle]) -> List[SwingPoint]:
        """Confirmed swing points (need `lookback` candles on each side), labelled HH/HL/LH/LL."""
        n, k = len(candles), self.lookback
        swings: List[SwingPoint] = []
        for i in range(k, n - k):
            window = candles[i - k:i + k + 1]
            if candles[i].high == max(c.high for c in window) and \
                    candles[i].high > max(c.high for c in window if c is not candles[i]):
                swings.append(SwingPoint(i, candles[i].high, True))
            if candles[i].low == min(c.low for c in window) and \
                    candles[i].low < min(c.low for c in window if c is not candles[i]):
                swings.append(SwingPoint(i, candles[i].low, False))
        last_high = last_low = None
        for s in swings:
            if s.is_high:
                if last_high is not None:
                    s.label = "HH" if s.price > last_high else "LH"
                last_high = s.price
            else:
                if last_low is not None:
                    s.label = "HL" if s.price > last_low else "LL"
                last_low = s.price
        return swings

    def analyze_structure(self, candles: Sequence[Candle], swings: Sequence[SwingPoint]):
        """Walk candles; emit BOS/CHoCH when a close breaks the last confirmed swing."""
        k = self.lookback
        breaks: List[StructureBreak] = []
        trend = Direction.NEUTRAL
        used = set()
        for i, c in enumerate(candles):
            # swings confirmed as of candle i
            confirmed = [s for s in swings if s.index + k <= i and (s.index, s.is_high) not in used]
            highs = [s for s in confirmed if s.is_high]
            lows = [s for s in confirmed if not s.is_high]
            if highs and c.close > highs[-1].price:
                s = highs[-1]
                kind = BreakType.CHOCH if trend == Direction.BEARISH else BreakType.BOS
                breaks.append(StructureBreak(i, kind, Direction.BULLISH, s.price, c.time))
                used.update((x.index, True) for x in highs)
                trend = Direction.BULLISH
            elif lows and c.close < lows[-1].price:
                s = lows[-1]
                kind = BreakType.CHOCH if trend == Direction.BULLISH else BreakType.BOS
                breaks.append(StructureBreak(i, kind, Direction.BEARISH, s.price, c.time))
                used.update((x.index, False) for x in lows)
                trend = Direction.BEARISH
        return trend, breaks

    # ---- zones -------------------------------------------------------------------------
    def find_order_blocks(self, candles: Sequence[Candle]) -> List[OrderBlock]:
        """Last opposite candle before a displacement candle that engulfs its range."""
        obs: List[OrderBlock] = []
        for i in range(self.atr_period, len(candles) - 1):
            atr = self.atr(candles, i)
            if atr <= 0:
                continue
            c, nxt = candles[i], candles[i + 1]
            if nxt.body < self.ob_impulse_atr * atr:
                continue
            if c.is_bearish and nxt.is_bullish and nxt.close > c.high:
                obs.append(OrderBlock(i, Direction.BULLISH, c.high, c.low, c.time))
            elif c.is_bullish and nxt.is_bearish and nxt.close < c.low:
                obs.append(OrderBlock(i, Direction.BEARISH, c.high, c.low, c.time))
        for ob in obs:
            for c in candles[ob.index + 2:]:
                if (ob.direction == Direction.BULLISH and c.close < ob.low) or \
                        (ob.direction == Direction.BEARISH and c.close > ob.high):
                    ob.mitigated = True
                    break
        return obs

    def find_fvgs(self, candles: Sequence[Candle]) -> List[FairValueGap]:
        fvgs: List[FairValueGap] = []
        for i in range(1, len(candles) - 1):
            a, b, c = candles[i - 1], candles[i], candles[i + 1]
            if c.low - a.high >= self.fvg_min and c.low > a.high:
                fvgs.append(FairValueGap(i, Direction.BULLISH, c.low, a.high, b.time))
            elif a.low - c.high >= self.fvg_min and a.low > c.high:
                fvgs.append(FairValueGap(i, Direction.BEARISH, a.low, c.high, b.time))
        for g in fvgs:
            for c in candles[g.index + 2:]:
                if (g.direction == Direction.BULLISH and c.close < g.bottom) or \
                        (g.direction == Direction.BEARISH and c.close > g.top):
                    g.filled = True
                    break
        return fvgs

    def find_equal_levels(self, swings: Sequence[SwingPoint]) -> List[EqualLevel]:
        out: List[EqualLevel] = []
        for is_high in (True, False):
            pts = [s for s in swings if s.is_high == is_high]
            used = set()
            for i, a in enumerate(pts):
                if i in used:
                    continue
                grp = [j for j, b in enumerate(pts) if j > i and abs(b.price - a.price) <= self.equal_tol]
                if grp:
                    used.update(grp)
                    members = [a] + [pts[j] for j in grp]
                    out.append(EqualLevel(sum(m.price for m in members) / len(members), is_high,
                                          [m.index for m in members]))
        return out

    def find_sweeps(self, candles: Sequence[Candle], swings: Sequence[SwingPoint]) -> List[LiquiditySweep]:
        """Wick through a prior swing level that closes back on the other side."""
        k = self.lookback
        sweeps: List[LiquiditySweep] = []
        taken = set()
        for i, c in enumerate(candles):
            for s in swings:
                if s.index + k >= i or (s.index, s.is_high) in taken:
                    continue
                if s.is_high and c.high > s.price and c.close < s.price:
                    sweeps.append(LiquiditySweep(i, Direction.BEARISH, s.price, c.time))
                    taken.add((s.index, True))
                elif not s.is_high and c.low < s.price and c.close > s.price:
                    sweeps.append(LiquiditySweep(i, Direction.BULLISH, s.price, c.time))
                    taken.add((s.index, False))
                elif (s.is_high and c.close > s.price) or (not s.is_high and c.close < s.price):
                    taken.add((s.index, s.is_high))  # genuinely broken, no longer a sweep target
        return sweeps

    # ---- main --------------------------------------------------------------------------
    def analyze(self, candles: Sequence[Candle], symbol: str = "") -> ICTAnalysis:
        if len(candles) < max(self.atr_period + 2, 2 * self.lookback + 3):
            return ICTAnalysis(symbol=symbol, last_index=len(candles) - 1)
        swings = self.find_swings(candles)
        trend, breaks = self.analyze_structure(candles, swings)
        return ICTAnalysis(
            symbol=symbol, trend=trend, swings=swings,
            order_blocks=self.find_order_blocks(candles),
            fvgs=self.find_fvgs(candles), breaks=breaks,
            sweeps=self.find_sweeps(candles, swings),
            equal_levels=self.find_equal_levels(swings),
            last_index=len(candles) - 1)

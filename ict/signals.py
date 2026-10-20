"""Signal generation from ICT analysis (multi-confluence, minimum RR enforced)."""
from __future__ import annotations

from typing import List, Optional, Sequence

from ict.structures import Candle, Direction, ICTAnalysis, Signal


class SignalGenerator:
    def __init__(self, pip_size: float = 0.0001, min_confluence: int = 2, min_rr: float = 2.0,
                 recent_bars: int = 20, sl_buffer_pips: float = 2.0,
                 min_sl_pips: float = 10.0) -> None:
        self.pip = pip_size
        self.min_confluence = min_confluence
        self.min_rr = max(min_rr, 2.0)
        self.recent = recent_bars
        self.buffer = sl_buffer_pips * pip_size
        self.min_risk = min_sl_pips * pip_size

    def generate(self, candles: Sequence[Candle], analysis: ICTAnalysis,
                 htf_trend: Optional[Direction] = None) -> Optional[Signal]:
        if not candles or analysis.last_index < 0:
            return None
        for direction in (Direction.BULLISH, Direction.BEARISH):
            sig = self._evaluate(candles, analysis, direction, htf_trend)
            if sig:
                return sig
        return None

    def _evaluate(self, candles: Sequence[Candle], a: ICTAnalysis, d: Direction,
                  htf: Optional[Direction]) -> Optional[Signal]:
        last = candles[-1]
        price = last.close
        since = a.last_index - self.recent
        bull = d == Direction.BULLISH
        if htf in (Direction.BULLISH, Direction.BEARISH) and htf != d:
            return None
        reasons: List[str] = []
        stops: List[float] = []

        sweeps = [s for s in a.sweeps if s.direction == d and s.index >= since]
        if sweeps:
            reasons.append(f"{'Sell' if bull else 'Buy'}-side liquidity sweep @ {sweeps[-1].level:.5f}")
            wick = min(c.low for c in candles[sweeps[-1].index:]) if bull else \
                max(c.high for c in candles[sweeps[-1].index:])
            stops.append(wick)
        brks = [b for b in a.breaks if b.direction == d and b.index >= since]
        if brks:
            reasons.append(f"{brks[-1].kind.value} {d.value} @ {brks[-1].level:.5f}")
        if a.trend == d or (htf == d):
            reasons.append(f"Trend/HTF {d.value}")
        obs = [o for o in a.order_blocks if o.direction == d and not o.mitigated
               and o.index >= since - self.recent and o.contains(price)]
        if obs:
            reasons.append(f"Price in {d.value} order block {obs[-1].low:.5f}-{obs[-1].high:.5f}")
            stops.append(obs[-1].low if bull else obs[-1].high)
        fvgs = [g for g in a.fvgs if g.direction == d and not g.filled
                and g.index >= since - self.recent and g.contains(price)]
        if fvgs:
            reasons.append(f"Price in {d.value} FVG {fvgs[-1].bottom:.5f}-{fvgs[-1].top:.5f}")
            stops.append(fvgs[-1].bottom if bull else fvgs[-1].top)

        # A zone/sweep is required so a stop can be placed; structure alone is not an entry.
        if not stops or len(reasons) < self.min_confluence:
            return None
        sl = (min(stops) - self.buffer) if bull else (max(stops) + self.buffer)
        if (bull and sl >= price) or (not bull and sl <= price):
            return None
        if abs(price - sl) < self.min_risk:  # avoid noise-level stops
            sl = price - self.min_risk if bull else price + self.min_risk
        risk = abs(price - sl)

        # Target: nearest opposing liquidity if it gives >= min RR, else min RR projection.
        levels = [s.price for s in a.swings if s.is_high == bull and (s.price > price if bull else s.price < price)]
        levels += [e.price for e in a.equal_levels if e.is_high == bull and (e.price > price if bull else e.price < price)]
        min_tp = price + self.min_rr * risk if bull else price - self.min_rr * risk
        far = [l for l in levels if (l >= min_tp if bull else l <= min_tp)]
        tp = (min(far) if bull else max(far)) if far else min_tp
        if far:
            reasons.append(f"Target opposing liquidity @ {tp:.5f}")
        return Signal(a.symbol, "buy" if bull else "sell", price, sl, tp, len(reasons), reasons, last.time)

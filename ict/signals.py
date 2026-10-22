"""Signal generation from ICT analysis (multi-confluence, minimum RR enforced)."""
from __future__ import annotations

from typing import List, Optional, Sequence

from config import Config
from ict.analyzer import ICTAnalyzer
from ict.structures import Candle, Direction, ICTAnalysis, Signal
from utils.logger import get_logger

log = get_logger("signals")


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


class ScalpSignalGenerator(SignalGenerator):
    """Stricter, multi-timeframe confirmed signals for fast M5 scalps.

    Every rejected setup records a reason in ``last_rejection`` / ``rejections`` and is logged.
    """

    def __init__(self, cfg: Config, pip_size: float = 0.0001) -> None:
        super().__init__(pip_size, cfg.min_confluence_scalp, cfg.min_rr_scalp,
                         recent_bars=cfg.recent_sweep_bars)
        self.min_rr = cfg.min_rr_scalp  # scalps may use less than the swing-trade 2:1 floor
        self.cfg = cfg
        self.helper = ICTAnalyzer(pip_size)
        self.rejections: List[str] = []
        self.last_rejection = ""
        self.last_score = 0.0

    def _reject(self, d: Optional[Direction], why: str) -> None:
        self.last_rejection = f"{d.value + ': ' if d else ''}{why}"
        self.rejections.append(self.last_rejection)
        log.debug("Scalp filter rejected - %s", self.last_rejection)

    def generate(self, candles: Sequence[Candle], analysis: ICTAnalysis,
                 htf_trend: Optional[Direction] = None,
                 htf_candles: Optional[Sequence[Candle]] = None,
                 htf_analysis: Optional[ICTAnalysis] = None) -> Optional[Signal]:
        self.rejections, self.last_rejection = [], ""
        if not candles or analysis.last_index < 0:
            return None
        cfg = self.cfg
        if self.helper.is_consolidating(candles, 10, cfg.min_range_pips):
            self._reject(None, "market consolidating (10-bar range too small)")
            return None
        ratio = self.helper.atr_ratio(candles)
        if ratio < cfg.min_atr_ratio:
            self._reject(None, f"ATR ratio {ratio:.2f} < {cfg.min_atr_ratio:.2f}")
            return None
        best: Optional[Signal] = None
        for d in (Direction.BULLISH, Direction.BEARISH):
            sig = self._scalp_eval(candles, analysis, d, htf_trend, htf_candles, htf_analysis)
            if sig and (best is None or sig.score > best.score):
                best = sig
        if best:
            log.info("Scalp signal accepted: %s confluence=%d score=%.2f RR=%.2f", best.side.upper(),
                     best.confluence, best.score, best.rr)
        return best

    def _htf_ok(self, d: Direction, htf_trend: Optional[Direction],
                htf_candles: Optional[Sequence[Candle]],
                htf_analysis: Optional[ICTAnalysis]) -> Optional[str]:
        """Return a rejection reason, or None when the HTF confirms direction `d`."""
        cfg = self.cfg
        if htf_trend is None and htf_analysis is not None:
            htf_trend = htf_analysis.trend
        if htf_trend != d:
            return "HTF trend neutral (chop)" if htf_trend in (None, Direction.NEUTRAL) \
                else "HTF trend opposes entry"
        if cfg.require_htf_structure and htf_analysis is not None:
            since = htf_analysis.last_index - cfg.htf_lookback_candles
            if not any(b.direction == d and b.index >= since for b in htf_analysis.breaks) and \
                    not any(s.direction == d and s.index >= since for s in htf_analysis.sweeps):
                return "no recent HTF break/sweep"
        if htf_candles:
            h = htf_candles[-1]
            if self.helper.body_ratio(h) < cfg.htf_min_body_ratio or \
                    (d == Direction.BULLISH) != h.is_bullish:
                return "weak HTF momentum"
        return None

    def _scalp_eval(self, candles: Sequence[Candle], a: ICTAnalysis, d: Direction,
                    htf_trend: Optional[Direction], htf_candles: Optional[Sequence[Candle]],
                    htf_analysis: Optional[ICTAnalysis]) -> Optional[Signal]:
        cfg, pip = self.cfg, self.pip
        bull = d == Direction.BULLISH
        last = candles[-1]
        price = last.close
        since = a.last_index - cfg.recent_sweep_bars
        reasons: List[str] = []
        stops: List[float] = []

        if cfg.enable_htf_confirmation:
            why = self._htf_ok(d, htf_trend, htf_candles, htf_analysis)
            if why:
                self._reject(d, why)
                return None
            reasons.append(f"HTF {d.value} aligned")

        sweeps = [s for s in a.sweeps if s.direction == d and s.index >= since]
        if not sweeps:
            self._reject(d, f"no liquidity sweep within {cfg.recent_sweep_bars} bars")
            return None
        sw = sweeps[-1]
        reasons.append(f"{'Sell' if bull else 'Buy'}-side liquidity sweep @ {sw.level:.5f}")
        stops.append(min(c.low for c in candles[sw.index:]) if bull else max(c.high for c in candles[sw.index:]))

        brks = [b for b in a.breaks if b.direction == d and b.index >= since]
        if brks:
            reasons.append(f"{brks[-1].kind.value} {d.value} close beyond {brks[-1].level:.5f}")
        elif cfg.require_close_confirmation:
            self._reject(d, "no close beyond last swing (wick only)")
            return None

        # entry candle: one of the last two candles must be strong & directional
        last2 = candles[-2:]
        if not any(c.is_bullish == bull and c.body > 0 and
                   self.helper.body_ratio(c) >= cfg.min_entry_candle_strength for c in last2):
            self._reject(d, f"entry candle body < {cfg.min_entry_candle_strength:.0%} of range")
            return None
        if cfg.reject_indecision and self.helper.is_indecision(last):
            self._reject(d, "indecision candle on entry")
            return None

        zone_mid = price
        score_bonus = 0.0
        obs = [o for o in a.order_blocks if o.direction == d and o.contains(price)
               and (not cfg.prioritize_unmitigated_ob or not o.mitigated)]
        if obs:
            ob = obs[-1]
            reasons.append(f"Price in unmitigated {d.value} order block {ob.low:.5f}-{ob.high:.5f}")
            stops.append(ob.low if bull else ob.high)
            zone_mid = (ob.low + ob.high) / 2
        fvgs = [g for g in a.fvgs if g.direction == d and not g.filled and g.contains(price)
                and g.index >= a.last_index - cfg.recent_fvg_bars]
        if fvgs:
            g = fvgs[-1]
            deep = g.size >= cfg.deep_fvg_min_pips * pip
            reasons.append(f"Price in recent {'deep ' if deep else ''}{d.value} FVG "
                           f"{g.bottom:.5f}-{g.top:.5f}")
            stops.append(g.bottom if bull else g.top)
            score_bonus += 1.0 if deep else 0.0
            if not obs:
                zone_mid = (g.bottom + g.top) / 2
        if not obs and not fvgs:
            self._reject(d, "no unmitigated order block / recent FVG at price")
            return None
        if len(reasons) < cfg.min_confluence_scalp:
            self._reject(d, f"confluence {len(reasons)} < {cfg.min_confluence_scalp}")
            return None

        # tight stop: nearest valid structural level beyond price
        valid = [x - self.buffer if bull else x + self.buffer for x in stops]
        valid = [x for x in valid if (x < price if bull else x > price)]
        if not valid:
            self._reject(d, "no valid stop level")
            return None
        sl = max(valid) if bull else min(valid)
        if abs(price - sl) < self.min_risk:
            sl = price - self.min_risk if bull else price + self.min_risk
        risk = abs(price - sl)
        if risk / pip > cfg.max_sl_pips_scalp + 1e-9:
            self._reject(d, f"SL {risk / pip:.1f} pips > {cfg.max_sl_pips_scalp:.0f}")
            return None

        lo, hi = cfg.min_rr_scalp * risk, cfg.max_rr_scalp * risk
        dist = min(max(cfg.scalp_tp_pips * pip, lo), hi)
        liq = [x.price for x in a.swings if x.is_high == bull and (x.price > price if bull else x.price < price)]
        liq += [e.price for e in a.equal_levels if e.is_high == bull and (e.price > price if bull else e.price < price)]
        near = [abs(x - price) for x in liq if lo <= abs(x - price) <= hi]
        if near:
            dist = min(near)
            reasons.append("Target opposing liquidity")
        tp = price + dist if bull else price - dist
        tp1 = price + risk if bull else price - risk

        bars_ago = a.last_index - sw.index
        recency = 1.0 / (1 + bars_ago)
        score = (len(reasons) + score_bonus) * recency / (1.0 + abs(price - zone_mid) / pip)
        self.last_score = score
        return Signal(a.symbol, "buy" if bull else "sell", price, sl, tp, len(reasons), reasons,
                      last.time, tp1=tp1, score=score)

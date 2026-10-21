"""Trade signal logic: runs ICT analysis on LTF + HTF candles and builds a signal."""
from __future__ import annotations

from typing import Optional, Sequence, Tuple

from brokers.base_connector import BaseConnector
from config import Config
from ict.analyzer import ICTAnalyzer
from ict.signals import SignalGenerator
from ict.structures import Candle, Direction, ICTAnalysis, Signal
from utils.logger import get_logger

log = get_logger("strategy")


def build_analyzer(cfg: Config, pip: float) -> ICTAnalyzer:
    return ICTAnalyzer(pip, cfg.swing_lookback, cfg.fvg_min_pips, cfg.ob_impulse_atr,
                       cfg.equal_level_tolerance_pips)


def evaluate(cfg: Config, symbol: str, pip: float, ltf: Sequence[Candle],
             htf: Optional[Sequence[Candle]] = None) -> Tuple[ICTAnalysis, Optional[Signal]]:
    """Pure function (no I/O) shared by live trading and backtesting."""
    analyzer = build_analyzer(cfg, pip)
    analysis = analyzer.analyze(ltf, symbol)
    htf_trend = analyzer.analyze(htf, symbol).trend if htf else None
    gen = SignalGenerator(pip, cfg.min_confluence, cfg.min_rr)
    return analysis, gen.generate(ltf, analysis, htf_trend)


class Strategy:
    def __init__(self, cfg: Config, connector: BaseConnector) -> None:
        self.cfg = cfg
        self.connector = connector

    def scan(self, symbol: str) -> Tuple[ICTAnalysis, Optional[Signal]]:
        pip = self.connector.get_symbol_spec(symbol).pip_size
        ltf = self.connector.get_candles(symbol, self.cfg.timeframe, 300)
        htf = None
        if self.cfg.htf_timeframe and self.cfg.htf_timeframe != self.cfg.timeframe:
            htf = self.connector.get_candles(symbol, self.cfg.htf_timeframe, 200)
        analysis, sig = evaluate(self.cfg, symbol, pip, ltf, htf)
        if sig:
            log.info("📈 SIGNAL %s %s entry %.5f SL %.5f TP %.5f RR %.1f | %s", sig.side.upper(),
                     symbol, sig.entry, sig.sl, sig.tp, sig.rr, "; ".join(sig.reasons))
        return analysis, sig

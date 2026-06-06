"""Quantitative Analyst — technical indicator convergence.

Computes RSI, MACD, EMA-cross, and VWAP and maps their agreement onto a
1..100 directional score. Uses ``pandas-ta`` if present, with hand-rolled
fallbacks so the agent works even before TA-Lib/pandas-ta are installed.
"""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd

from core.base_agent import NEUTRAL_SCORE, BaseAgent, clamp_score
from core.enums import AgentRole
from core.models import AgentEvaluation, AnalysisContext

logger = logging.getLogger(__name__)

try:  # optional dependency
    import pandas_ta as ta  # type: ignore

    _HAS_PANDAS_TA = True
except Exception:  # noqa: BLE001
    _HAS_PANDAS_TA = False


class TechnicalAgent(BaseAgent):
    role = AgentRole.TECHNICAL

    def __init__(self, *, weight: float = 0.5, min_bars: int = 50) -> None:
        super().__init__(weight=weight)
        self.min_bars = min_bars

    async def evaluate(self, ctx: AnalysisContext) -> AgentEvaluation:
        bars = ctx.bars
        if bars is None or len(bars) < self.min_bars:
            return AgentEvaluation(
                role=self.role,
                score=NEUTRAL_SCORE,
                confidence=0.2,
                rationale=f"insufficient bars (<{self.min_bars})",
            )

        df = bars.copy()
        signals: dict[str, float] = {}

        rsi = self._rsi(df["close"])
        # RSI: 30 -> bullish reversal zone, 70 -> bearish; center 50.
        signals["rsi"] = float(np.interp(rsi, [20, 50, 80], [80, 50, 20]))

        macd_hist = self._macd_hist(df["close"])
        signals["macd"] = 70.0 if macd_hist > 0 else 30.0

        ema_fast = df["close"].ewm(span=9, adjust=False).mean().iloc[-1]
        ema_slow = df["close"].ewm(span=21, adjust=False).mean().iloc[-1]
        signals["ema_cross"] = 75.0 if ema_fast > ema_slow else 25.0

        vwap = self._vwap(df)
        last = float(df["close"].iloc[-1])
        signals["vwap"] = 65.0 if last > vwap else 35.0

        clean = {k: v for k, v in signals.items() if not np.isnan(v)}
        if not clean:
            return AgentEvaluation(role=self.role, score=NEUTRAL_SCORE, confidence=0.2, rationale="no valid signals")
        # Convergence = mean of sub-signals; confidence rises with agreement.
        score = clamp_score(float(np.mean(list(clean.values()))))
        spread = float(np.std(list(clean.values())))
        confidence = float(max(0.3, 1.0 - spread / 50.0))

        return AgentEvaluation(
            role=self.role,
            score=score,
            confidence=confidence,
            rationale=(
                f"RSI={rsi:.1f} MACDhist={macd_hist:.4f} "
                f"EMA9{'>' if ema_fast > ema_slow else '<'}EMA21 "
                f"px{'>' if last > vwap else '<'}VWAP"
            ),
            data={"signals": signals, "rsi": rsi, "vwap": vwap},
        )

    # --- indicator helpers (pandas-ta if available, else manual) --------
    def _rsi(self, close: pd.Series, length: int = 14) -> float:
        if _HAS_PANDAS_TA:
            return float(ta.rsi(close, length=length).iloc[-1])
        delta = close.diff()
        gain = delta.clip(lower=0).rolling(length).mean().iloc[-1]
        loss = (-delta.clip(upper=0)).rolling(length).mean().iloc[-1]
        if np.isnan(gain) or np.isnan(loss):
            return 50.0
        if loss == 0:  # no down-moves -> max strength
            return 100.0 if gain > 0 else 50.0
        rs = gain / loss
        return float(100 - 100 / (1 + rs))

    def _macd_hist(self, close: pd.Series) -> float:
        if _HAS_PANDAS_TA:
            macd = ta.macd(close)
            return float(macd.iloc[-1, -1])  # histogram column
        ema12 = close.ewm(span=12, adjust=False).mean()
        ema26 = close.ewm(span=26, adjust=False).mean()
        macd_line = ema12 - ema26
        signal = macd_line.ewm(span=9, adjust=False).mean()
        return float((macd_line - signal).iloc[-1])

    def _vwap(self, df: pd.DataFrame) -> float:
        typical = (df["high"] + df["low"] + df["close"]) / 3.0
        cum_vol = df["volume"].cumsum().replace(0, np.nan)
        return float((typical * df["volume"]).cumsum().iloc[-1] / cum_vol.iloc[-1])

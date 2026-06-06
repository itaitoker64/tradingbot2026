"""Strongly-typed data containers passed between agents and the orchestrator.

Keeping these as frozen dataclasses makes the flow through the multi-agent
pipeline explicit and prevents agents from mutating shared state.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping, Optional, Sequence

import pandas as pd

from .enums import AgentRole, Decision, OrderSide


def _utcnow() -> datetime:
    return datetime.now(tz=timezone.utc)


@dataclass(slots=True)
class AnalysisContext:
    """Everything an agent might need to evaluate a single ticker.

    Built once per evaluation cycle by the Portfolio Manager and shared
    (read-only) across all agents to avoid redundant data fetches.
    """

    ticker: str
    as_of: datetime = field(default_factory=_utcnow)
    # OHLCV history; columns: open, high, low, close, volume (DatetimeIndex).
    bars: Optional[pd.DataFrame] = None
    # Path or bytes of a rendered chart for the Vision agent.
    chart_image_path: Optional[str] = None
    # Live account snapshot for the Risk agent (equity, buying_power, ...).
    account: Mapping[str, float] = field(default_factory=dict)
    # Free-form extras (e.g. sector, earnings_date).
    meta: Mapping[str, Any] = field(default_factory=dict)

    @property
    def last_price(self) -> Optional[float]:
        if self.bars is None or self.bars.empty:
            return None
        return float(self.bars["close"].iloc[-1])


@dataclass(slots=True, frozen=True)
class AgentEvaluation:
    """Result returned by a single agent.

    ``score`` is always in [1, 100] (1 = extreme bearish, 100 = extreme
    bullish). ``veto`` lets an agent (in practice, Risk) hard-block a trade
    regardless of the weighted composite.
    """

    role: AgentRole
    score: int
    confidence: float = 1.0  # 0..1, how sure the agent is of its own score
    veto: bool = False
    rationale: str = ""
    data: Mapping[str, Any] = field(default_factory=dict)


@dataclass(slots=True, frozen=True)
class RiskParameters:
    """Concrete trade plan produced by the Risk agent."""

    qty: float
    entry: float
    stop_loss: float
    take_profit: float
    risk_reward: float
    risk_per_trade_usd: float


@dataclass(slots=True, frozen=True)
class TradeDecision:
    """Final, executable decision from the Portfolio Manager."""

    ticker: str
    decision: Decision
    composite_score: float
    side: Optional[OrderSide] = None
    risk: Optional[RiskParameters] = None
    evaluations: Sequence[AgentEvaluation] = field(default_factory=tuple)
    as_of: datetime = field(default_factory=_utcnow)

    @property
    def is_actionable(self) -> bool:
        return self.decision in (Decision.LONG, Decision.SHORT) and self.risk is not None

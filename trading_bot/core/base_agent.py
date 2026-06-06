"""Abstract base class for every agent on the desk.

Contract:
    * Subclasses implement ``evaluate(ctx) -> AgentEvaluation`` (async).
    * The public ``get_score(ticker) -> int`` interface is provided by this
      base class, is awaitable, always returns a clamped int in [1, 100],
      and never raises out of an agent (failures degrade to a neutral 50
      with a logged rationale, so one flaky agent can't crash the desk).
"""
from __future__ import annotations

import abc
import logging
from typing import Final

from .enums import AgentRole
from .models import AgentEvaluation, AnalysisContext

logger = logging.getLogger(__name__)

SCORE_MIN: Final[int] = 1
SCORE_MAX: Final[int] = 100
NEUTRAL_SCORE: Final[int] = 50


def clamp_score(value: float) -> int:
    """Coerce any numeric score into the valid [1, 100] integer band."""
    return int(max(SCORE_MIN, min(SCORE_MAX, round(value))))


class BaseAgent(abc.ABC):
    """Common lifecycle, scoring guardrails, and weight metadata."""

    role: AgentRole

    def __init__(self, *, weight: float = 1.0, name: str | None = None) -> None:
        if weight < 0:
            raise ValueError("weight must be non-negative")
        self.weight = weight
        self.name = name or self.__class__.__name__

    # --- subclass hook --------------------------------------------------
    @abc.abstractmethod
    async def evaluate(self, ctx: AnalysisContext) -> AgentEvaluation:
        """Produce a full evaluation for the ticker described by ``ctx``."""
        raise NotImplementedError

    # --- public interface ----------------------------------------------
    async def get_score(self, ctx: AnalysisContext) -> int:
        """Return only the [1, 100] score. Safe wrapper around ``evaluate``."""
        evaluation = await self.safe_evaluate(ctx)
        return evaluation.score

    async def safe_evaluate(self, ctx: AnalysisContext) -> AgentEvaluation:
        """Run ``evaluate`` with hard validation + failure isolation."""
        try:
            result = await self.evaluate(ctx)
        except Exception:  # noqa: BLE001 - intentional desk-level isolation
            logger.exception("%s failed on %s; degrading to neutral", self.name, ctx.ticker)
            return AgentEvaluation(
                role=self.role,
                score=NEUTRAL_SCORE,
                confidence=0.0,
                rationale="agent error -> neutral fallback",
            )
        # Enforce the contract no matter what the subclass returned.
        clamped = clamp_score(result.score)
        if clamped != result.score:
            result = AgentEvaluation(
                role=result.role,
                score=clamped,
                confidence=result.confidence,
                veto=result.veto,
                rationale=result.rationale,
                data=result.data,
            )
        return result

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<{self.name} role={self.role.value} weight={self.weight}>"

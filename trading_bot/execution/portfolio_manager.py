"""Portfolio Manager — the orchestrator / execution brain.

Pipeline per ticker:
    1. Build an AnalysisContext (bars + account + chart) once.
    2. Run Fundamental, Vision, Technical, Risk agents CONCURRENTLY.
    3. Blend the three directional scores by configured weights ->
       composite in [1, 100].
    4. Map composite to LONG / SHORT / PASS via thresholds.
    5. Apply the Risk veto and minimum-risk-score gate.
    6. Ask Risk for a concrete plan in the chosen direction; size the order.
    7. Route a bracket order through the injected broker.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Optional, Sequence

from config.settings import DecisionThresholds, Settings
from core.enums import AgentRole, Decision, OrderSide
from core.models import AgentEvaluation, AnalysisContext, TradeDecision
from agents.fundamental_agent import FundamentalAgent
from agents.risk_agent import RiskAgent
from agents.technical_agent import TechnicalAgent
from agents.vision_agent import VisionAgent
from execution.base_broker import BaseBroker, OrderReceipt

logger = logging.getLogger(__name__)


class PortfolioManager:
    def __init__(
        self,
        *,
        settings: Settings,
        broker: BaseBroker,
        fundamental: FundamentalAgent,
        vision: VisionAgent,
        technical: TechnicalAgent,
        risk: RiskAgent,
    ) -> None:
        self.settings = settings
        self.broker = broker
        self.fundamental = fundamental
        self.vision = vision
        self.technical = technical
        self.risk = risk
        self._weights = settings.weights.as_map()
        self._thresholds: DecisionThresholds = settings.thresholds

    # --- decision -------------------------------------------------------
    async def decide(self, ctx: AnalysisContext) -> TradeDecision:
        fundamental, vision, technical, risk = await asyncio.gather(
            self.fundamental.safe_evaluate(ctx),
            self.vision.safe_evaluate(ctx),
            self.technical.safe_evaluate(ctx),
            self.risk.safe_evaluate(ctx),
        )
        evaluations = (fundamental, vision, technical, risk)

        composite = self._composite(fundamental, vision, technical)
        decision = self._direction(composite)

        # Risk gates.
        if risk.veto:
            logger.info("%s vetoed by Risk: %s", ctx.ticker, risk.rationale)
            decision = Decision.PASS
        elif risk.score < self._thresholds.min_risk_score:
            logger.info("%s blocked: risk score %d < %d", ctx.ticker, risk.score, self._thresholds.min_risk_score)
            decision = Decision.PASS

        if decision is Decision.PASS:
            return TradeDecision(
                ticker=ctx.ticker,
                decision=Decision.PASS,
                composite_score=composite,
                evaluations=evaluations,
            )

        # Build a concrete plan in the chosen direction.
        plan = self.risk.build_plan(ctx, intended=decision)
        if plan is None or plan.qty <= 0 or plan.risk_reward < self.settings.risk.min_risk_reward:
            logger.info("%s downgraded to PASS: no viable plan", ctx.ticker)
            return TradeDecision(
                ticker=ctx.ticker,
                decision=Decision.PASS,
                composite_score=composite,
                evaluations=evaluations,
            )

        side = OrderSide.BUY if decision is Decision.LONG else OrderSide.SELL
        return TradeDecision(
            ticker=ctx.ticker,
            decision=decision,
            composite_score=composite,
            side=side,
            risk=plan,
            evaluations=evaluations,
        )

    # --- execution ------------------------------------------------------
    async def execute(self, decision: TradeDecision) -> Optional[OrderReceipt]:
        if not decision.is_actionable:
            return None
        receipt = await self.broker.submit_bracket(decision)
        if receipt:
            logger.info("ORDER %s %s -> %s (%s)", decision.decision.value, decision.ticker, receipt.order_id, receipt.status)
        return receipt

    async def run_once(self, ctx: AnalysisContext, *, execute: bool = True) -> TradeDecision:
        decision = await self.decide(ctx)
        if execute and decision.is_actionable:
            await self.execute(decision)
        return decision

    # --- math -----------------------------------------------------------
    def _composite(self, f: AgentEvaluation, v: AgentEvaluation, t: AgentEvaluation) -> float:
        by_role = {AgentRole.FUNDAMENTAL: f, AgentRole.VISION: v, AgentRole.TECHNICAL: t}
        # Confidence-adjusted weighted mean: an unsure agent is pulled toward
        # neutral so it neither dominates nor randomly flips the decision.
        num = den = 0.0
        for key, role in (
            ("fundamental", AgentRole.FUNDAMENTAL),
            ("vision", AgentRole.VISION),
            ("technical", AgentRole.TECHNICAL),
        ):
            ev = by_role[role]
            w = self._weights[key] * max(ev.confidence, 0.05)
            num += ev.score * w
            den += w
        return round(num / den, 2) if den else 50.0

    def _direction(self, composite: float) -> Decision:
        if composite >= self._thresholds.long_above:
            return Decision.LONG
        if composite <= self._thresholds.short_below:
            return Decision.SHORT
        return Decision.PASS

    @staticmethod
    def summarise(evaluations: Sequence[AgentEvaluation]) -> str:
        return " | ".join(f"{e.role.value}:{e.score}({e.confidence:.2f})" for e in evaluations)

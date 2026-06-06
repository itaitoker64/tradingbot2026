"""Fundamental Analyst — news & sentiment.

Pulls headlines from an injected ``NewsSource`` and converts them into a
1..100 bullishness score. Two scoring backends:

    * LLM backend (preferred): asks an Anthropic model to score aggregate
      sentiment. Requires ANTHROPIC_API_KEY.
    * Lexical fallback: a transparent keyword heuristic so the agent still
      produces a sane score offline / without an API key.
"""
from __future__ import annotations

import json
import logging
from typing import Sequence

from core.base_agent import NEUTRAL_SCORE, BaseAgent, clamp_score
from core.enums import AgentRole
from core.models import AgentEvaluation, AnalysisContext
from data.news_sources import Headline, NewsSource

logger = logging.getLogger(__name__)

_BULL = {"beat", "surge", "soar", "upgrade", "record", "growth", "buyback", "outperform", "rally"}
_BEAR = {"miss", "plunge", "downgrade", "lawsuit", "probe", "cut", "warning", "recall", "bankruptcy"}


class FundamentalAgent(BaseAgent):
    role = AgentRole.FUNDAMENTAL

    def __init__(
        self,
        news_source: NewsSource,
        *,
        weight: float = 0.3,
        anthropic_api_key: str = "",
        model: str = "claude-3-5-sonnet-latest",
        max_headlines: int = 20,
    ) -> None:
        super().__init__(weight=weight)
        self.news = news_source
        self.api_key = anthropic_api_key
        self.model = model
        self.max_headlines = max_headlines

    async def evaluate(self, ctx: AnalysisContext) -> AgentEvaluation:
        headlines = await self.news.fetch_headlines(ctx.ticker, limit=self.max_headlines)
        if not headlines:
            return AgentEvaluation(
                role=self.role,
                score=NEUTRAL_SCORE,
                confidence=0.1,
                rationale="no headlines available",
            )

        if self.api_key:
            score, rationale = await self._score_with_llm(ctx.ticker, headlines)
        else:
            score, rationale = self._score_lexical(headlines)

        confidence = min(1.0, 0.4 + 0.04 * len(headlines))
        return AgentEvaluation(
            role=self.role,
            score=clamp_score(score),
            confidence=confidence,
            rationale=rationale,
            data={"n_headlines": len(headlines)},
        )

    # --- backends -------------------------------------------------------
    async def _score_with_llm(self, ticker: str, headlines: Sequence[Headline]) -> tuple[int, str]:
        import anthropic  # lazy import

        client = anthropic.AsyncAnthropic(api_key=self.api_key)
        digest = "\n".join(f"- {h.title}: {h.summary}" for h in headlines[: self.max_headlines])
        prompt = (
            f"You are a sell-side analyst. Score the aggregate near-term news "
            f"sentiment for {ticker} on an integer scale 1 (extreme bearish) to "
            f"100 (extreme bullish). Respond ONLY with JSON: "
            f'{{"score": <int>, "reason": "<=20 words"}}.\n\nHeadlines:\n{digest}'
        )
        try:
            resp = await client.messages.create(
                model=self.model,
                max_tokens=200,
                messages=[{"role": "user", "content": prompt}],
            )
            text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
            parsed = json.loads(text)
            return int(parsed["score"]), str(parsed.get("reason", ""))
        except Exception:  # noqa: BLE001
            logger.exception("LLM sentiment failed; falling back to lexical")
            return self._score_lexical(headlines)

    def _score_lexical(self, headlines: Sequence[Headline]) -> tuple[int, str]:
        bull = bear = 0
        for h in headlines:
            words = f"{h.title} {h.summary}".lower().split()
            bull += sum(w in _BULL for w in words)
            bear += sum(w in _BEAR for w in words)
        total = bull + bear
        if total == 0:
            return NEUTRAL_SCORE, "neutral (no sentiment keywords)"
        ratio = bull / total
        return clamp_score(1 + ratio * 99), f"lexical bull={bull} bear={bear}"

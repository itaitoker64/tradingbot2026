"""Composition root + run loops.

Usage:
    RUN_MODE=backtest python main.py AAPL MSFT
    RUN_MODE=live     python main.py AAPL

Wires every dependency, then either replays history through the MAS
(backtest) or polls live and routes orders (live). Defaults to DRY RUN in
live mode unless EXECUTE_LIVE=true is set — trade with real money only
deliberately.
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys
from typing import Sequence

from config.settings import Settings, load_settings
from core.enums import RunMode
from core.models import AnalysisContext
from data.chart_renderer import render_chart
from data.news_sources import AlpacaNewsSource, NewsSource, PoliStockSource
from agents.fundamental_agent import FundamentalAgent
from agents.risk_agent import RiskAgent
from agents.technical_agent import TechnicalAgent
from agents.vision_agent import VisionAgent
from execution.alpaca_broker import AlpacaBroker
from execution.base_broker import BaseBroker
from execution.ibkr_broker import IBKRBroker
from execution.portfolio_manager import PortfolioManager

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s | %(message)s")
logger = logging.getLogger("desk")


def build_broker(settings: Settings) -> BaseBroker:
    if settings.run_mode is RunMode.LIVE:
        return IBKRBroker(settings.ibkr_host, settings.ibkr_port, settings.ibkr_client_id)
    return AlpacaBroker(settings.alpaca_key_id, settings.alpaca_secret, paper=settings.alpaca_paper)


def build_news_source(settings: Settings) -> NewsSource:
    # Prefer Alpaca news if keys exist; else the PoliStock stub.
    if settings.alpaca_key_id and settings.alpaca_secret:
        return AlpacaNewsSource(settings.alpaca_key_id, settings.alpaca_secret)
    return PoliStockSource(settings.news_base_url, settings.news_api_key)


def build_manager(settings: Settings, broker: BaseBroker) -> PortfolioManager:
    news = build_news_source(settings)
    return PortfolioManager(
        settings=settings,
        broker=broker,
        fundamental=FundamentalAgent(
            news,
            weight=settings.weights.fundamental,
            anthropic_api_key=settings.anthropic_api_key,
            model=settings.llm_model,
        ),
        vision=VisionAgent(
            weight=settings.weights.vision,
            anthropic_api_key=settings.anthropic_api_key,
            model=settings.llm_model,
        ),
        technical=TechnicalAgent(weight=settings.weights.technical),
        risk=RiskAgent(settings.risk),
    )


async def evaluate_ticker(pm: PortfolioManager, broker: BaseBroker, ticker: str, *, execute: bool) -> None:
    bars = await broker.get_bars(ticker, timeframe="5Min", limit=200)
    account = await broker.get_account()
    chart = render_chart(ticker, bars)
    ctx = AnalysisContext(ticker=ticker, bars=bars, account=account, chart_image_path=chart)

    decision = await pm.run_once(ctx, execute=execute)
    logger.info(
        "%s -> %s | composite=%.1f | %s",
        ticker,
        decision.decision.value,
        decision.composite_score,
        pm.summarise(decision.evaluations),
    )
    if decision.is_actionable and decision.risk:
        r = decision.risk
        logger.info("   plan qty=%g entry=%.2f SL=%.2f TP=%.2f R/R=%.2f", r.qty, r.entry, r.stop_loss, r.take_profit, r.risk_reward)


async def main(tickers: Sequence[str]) -> None:
    settings = load_settings()
    logger.info("run_mode=%s tickers=%s", settings.run_mode.value, list(tickers))

    broker = build_broker(settings)
    pm = build_manager(settings, broker)

    execute = settings.run_mode is RunMode.BACKTEST or os.environ.get("EXECUTE_LIVE", "false").lower() == "true"
    if settings.run_mode is RunMode.LIVE and not execute:
        logger.warning("LIVE mode but EXECUTE_LIVE!=true -> DRY RUN (no orders sent)")

    async with broker:
        for ticker in tickers:
            try:
                await evaluate_ticker(pm, broker, ticker, execute=execute)
            except Exception:  # noqa: BLE001
                logger.exception("evaluation failed for %s", ticker)


if __name__ == "__main__":
    syms = sys.argv[1:] or ["AAPL"]
    asyncio.run(main(syms))

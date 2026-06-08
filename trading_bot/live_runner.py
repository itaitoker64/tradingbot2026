"""Heartbeat-driven live runner.

Replaces the one-shot polling loop in main.py for LIVE mode.
Instead of evaluating tickers on a fixed interval, the bot:

  1. Runs an initial evaluation of all configured tickers on startup.
  2. Subscribes to the AI4Trade heartbeat loop.
  3. Re-evaluates any ticker mentioned in incoming platform tasks/messages.
  4. Re-evaluates all tickers every RESCAN_INTERVAL_MIN minutes regardless.

This is event-driven rather than time-driven — the bot reacts to the
community instead of blindly polling.

Usage:
    python live_runner.py AAPL MSFT NVDA TSLA
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys
from datetime import datetime, timezone
from typing import Sequence

from dotenv import load_dotenv

load_dotenv()

from config.settings import load_settings
from core.models import AnalysisContext
from data.chart_renderer import render_chart
from data.ai4trade_client import AI4TradeClient
from data.market_intel_source import CombinedNewsSource, MarketIntelNewsSource
from data.news_sources import AlpacaNewsSource, PoliStockSource
from agents.fundamental_agent import FundamentalAgent
from agents.liquid_agent import LiquidAgent
from agents.risk_agent import RiskAgent
from agents.social_agent import SocialSentimentAgent
from agents.technical_agent import TechnicalAgent
from agents.vision_agent import VisionAgent
from execution.alpaca_broker import AlpacaBroker
from execution.base_broker import BaseBroker
from execution.ibkr_broker import IBKRBroker
from execution.portfolio_manager import PortfolioManager
from execution.signal_publisher import SignalPublisher

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s | %(message)s")
logger = logging.getLogger("live")

RESCAN_INTERVAL_MIN = int(os.environ.get("RESCAN_INTERVAL_MIN", "30"))


async def evaluate_ticker(
    pm: PortfolioManager,
    broker: BaseBroker,
    ticker: str,
    *,
    execute: bool,
    publisher: SignalPublisher | None,
) -> None:
    try:
        bars = await broker.get_bars(ticker, timeframe="5Min", limit=200)
        account = await broker.get_account()
        chart = render_chart(ticker, bars)
        ctx = AnalysisContext(ticker=ticker, bars=bars, account=account, chart_image_path=chart)
        decision = await pm.run_once(ctx, execute=execute)
        logger.info(
            "%s -> %s | composite=%.1f | %s",
            ticker, decision.decision.value, decision.composite_score,
            pm.summarise(decision.evaluations),
        )
        if publisher:
            await publisher.publish(decision)
    except Exception:
        logger.exception("evaluation failed for %s", ticker)


async def handle_heartbeat(
    messages: list,
    tasks: list,
    pm: PortfolioManager,
    broker: BaseBroker,
    default_tickers: list[str],
    *,
    execute: bool,
    publisher: SignalPublisher | None,
) -> None:
    """React to heartbeat events — re-evaluate tickers mentioned in messages."""
    triggered: set[str] = set()

    for msg in messages:
        logger.info(
            "AI4Trade [%s]: %s",
            msg.get("type", "?"),
            msg.get("content", "")[:100],
        )
        # If someone mentions a ticker we track, re-run analysis
        data = msg.get("data") or {}
        symbol = data.get("symbol") or data.get("ticker")
        if symbol and symbol.upper() in {t.upper() for t in default_tickers}:
            triggered.add(symbol.upper())

    for task in tasks:
        logger.info("AI4Trade task: %s", task.get("type"))
        inp = task.get("input_data") or {}
        symbol = inp.get("symbol") or inp.get("ticker")
        if symbol:
            triggered.add(symbol.upper())

    if triggered:
        logger.info("Heartbeat triggered re-eval for: %s", triggered)
        await asyncio.gather(
            *[evaluate_ticker(pm, broker, t, execute=execute, publisher=publisher) for t in triggered],
            return_exceptions=True,
        )


async def rescan_loop(
    pm: PortfolioManager,
    broker: BaseBroker,
    tickers: list[str],
    *,
    execute: bool,
    publisher: SignalPublisher | None,
    interval_min: int,
) -> None:
    """Re-evaluate all tickers every interval_min minutes."""
    while True:
        await asyncio.sleep(interval_min * 60)
        logger.info("Scheduled rescan of %s", tickers)
        await asyncio.gather(
            *[evaluate_ticker(pm, broker, t, execute=execute, publisher=publisher) for t in tickers],
            return_exceptions=True,
        )


async def main(tickers: Sequence[str]) -> None:
    settings = load_settings()
    execute = os.environ.get("EXECUTE_LIVE", "false").lower() == "true"

    if not execute:
        logger.warning("EXECUTE_LIVE!=true → DRY RUN (analysis only, no orders sent)")

    # --- build broker -------------------------------------------------
    broker: BaseBroker = AlpacaBroker(
        settings.alpaca_key_id, settings.alpaca_secret, paper=True
    )

    # --- build AI4Trade client ----------------------------------------
    ai4 = AI4TradeClient()
    await ai4.__aenter__()

    # --- build news sources -------------------------------------------
    alpaca_news = AlpacaNewsSource(settings.alpaca_key_id, settings.alpaca_secret) \
        if settings.alpaca_key_id else PoliStockSource(settings.news_base_url, settings.news_api_key)
    intel_news = MarketIntelNewsSource(ai4)
    news = CombinedNewsSource(alpaca_news, intel_news)

    # --- build agents -------------------------------------------------
    social = SocialSentimentAgent(ai4, weight=settings.weights.social)
    pm = PortfolioManager(
        settings=settings,
        broker=broker,
        fundamental=FundamentalAgent(news, weight=settings.weights.fundamental,
                                     anthropic_api_key=settings.anthropic_api_key,
                                     model=settings.llm_model),
        vision=VisionAgent(weight=settings.weights.vision,
                           anthropic_api_key=settings.anthropic_api_key,
                           model=settings.llm_model),
        technical=TechnicalAgent(weight=settings.weights.technical),
        risk=RiskAgent(settings.risk),
        liquid=LiquidAgent(weight=settings.weights.liquid) if settings.weights.liquid > 0 else None,
        social=social,
    )

    publisher = SignalPublisher(ai4, publish_pass=True) if ai4.token else None
    tickers_list = list(tickers)

    async with broker:
        # Initial scan
        logger.info("Initial scan of %s", tickers_list)
        await asyncio.gather(
            *[evaluate_ticker(pm, broker, t, execute=execute, publisher=publisher) for t in tickers_list],
            return_exceptions=True,
        )

        # Run heartbeat + rescan concurrently
        async def hb_callback(messages, tasks):
            await handle_heartbeat(messages, tasks, pm, broker, tickers_list,
                                   execute=execute, publisher=publisher)

        await asyncio.gather(
            ai4.heartbeat_loop(hb_callback),
            rescan_loop(pm, broker, tickers_list, execute=execute,
                        publisher=publisher, interval_min=RESCAN_INTERVAL_MIN),
        )

    await ai4.__aexit__(None, None, None)


if __name__ == "__main__":
    syms = sys.argv[1:] or ["AAPL"]
    asyncio.run(main(syms))

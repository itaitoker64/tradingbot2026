"""Composition root + run loops.

Usage:
    RUN_MODE=backtest python main.py AAPL MSFT        # Alpaca paper + AI4Trade signals
    RUN_MODE=live    python main.py AAPL              # IBKR or Liquid live
    python live_runner.py AAPL MSFT                  # Heartbeat-driven live mode (recommended)
    python challenge_runner.py AAPL BTC              # AI4Trade challenge competition
    python backtest_runner.py AAPL MSFT NVDA         # Walk-forward backtest + dashboard
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys
from typing import Sequence

from dotenv import load_dotenv
load_dotenv()

from config.settings import Settings, load_settings
from core.enums import RunMode
from core.models import AnalysisContext
from data.chart_renderer import render_chart
from data.ai4trade_client import AI4TradeClient
from data.market_intel_source import CombinedNewsSource, MarketIntelNewsSource
from data.news_sources import AlpacaNewsSource, NewsSource, PoliStockSource
from data.sector_scanner import SectorScanner
from agents.fundamental_agent import FundamentalAgent
from agents.liquid_agent import LiquidAgent
from agents.regime_agent import detect_regime
from agents.risk_agent import RiskAgent
from agents.social_agent import SocialSentimentAgent
from agents.technical_agent import TechnicalAgent
from agents.vision_agent import VisionAgent
from execution.alpaca_broker import AlpacaBroker
from execution.base_broker import BaseBroker
from execution.ibkr_broker import IBKRBroker
from execution.liquid_broker import LiquidBroker
from execution.portfolio_manager import PortfolioManager
from execution.signal_publisher import SignalPublisher

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s | %(message)s")
logger = logging.getLogger("desk")


def build_broker(settings: Settings) -> BaseBroker:
    if settings.run_mode is RunMode.LIVE and settings.use_liquid_broker:
        return LiquidBroker(settings.liquid_api_key)
    if settings.run_mode is RunMode.LIVE:
        return IBKRBroker(settings.ibkr_host, settings.ibkr_port, settings.ibkr_client_id)
    return AlpacaBroker(settings.alpaca_key_id, settings.alpaca_secret, paper=settings.alpaca_paper)


def build_manager(settings: Settings, broker: BaseBroker, ai4: AI4TradeClient) -> PortfolioManager:
    alpaca_news: NewsSource = (
        AlpacaNewsSource(settings.alpaca_key_id, settings.alpaca_secret)
        if settings.alpaca_key_id
        else PoliStockSource(settings.news_base_url, settings.news_api_key)
    )
    news = CombinedNewsSource(alpaca_news, MarketIntelNewsSource(ai4))
    publisher = SignalPublisher(ai4, publish_pass=True) if ai4.token and settings.ai4trade_publish else None

    return PortfolioManager(
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
        social=SocialSentimentAgent(ai4, weight=settings.weights.social) if settings.weights.social > 0 else None,
        publisher=publisher,
    )


async def evaluate_ticker(pm: PortfolioManager, broker: BaseBroker, ticker: str, *, execute: bool) -> None:
    bars = await broker.get_bars(ticker, timeframe="5Min", limit=200)
    account = await broker.get_account()
    chart = render_chart(ticker, bars)
    ctx = AnalysisContext(ticker=ticker, bars=bars, account=account, chart_image_path=chart)
    decision = await pm.run_once(ctx, execute=execute)
    logger.info("%s -> %s | composite=%.1f | %s",
                ticker, decision.decision.value, decision.composite_score,
                pm.summarise(decision.evaluations))
    if decision.is_actionable and decision.risk:
        r = decision.risk
        logger.info("  plan qty=%g entry=%.2f SL=%.2f TP=%.2f R/R=%.2f",
                    r.qty, r.entry, r.stop_loss, r.take_profit, r.risk_reward)


async def main(tickers: Sequence[str]) -> None:
    settings = load_settings()
    logger.info("run_mode=%s tickers=%s", settings.run_mode.value, list(tickers))

    ai4 = AI4TradeClient(
        email=settings.ai4trade_email,
        password=settings.ai4trade_password,
        bot_name=settings.ai4trade_bot_name,
    )
    await ai4.__aenter__()

    broker = build_broker(settings)
    pm = build_manager(settings, broker, ai4)

    execute = (
        settings.run_mode is RunMode.BACKTEST
        or os.environ.get("EXECUTE_LIVE", "false").lower() == "true"
    )
    if settings.run_mode is RunMode.LIVE and not execute:
        logger.warning("LIVE mode but EXECUTE_LIVE!=true → DRY RUN")

    async with broker:
        # ── 1. Market regime (runs once, adjusts thresholds for all tickers) ──
        regime = await detect_regime(broker)
        pm.set_regime(regime)

        # ── 2. Sector scan (hot sectors → prioritise tickers) ─────────────────
        scanner = SectorScanner(broker)
        scan    = await scanner.scan(list(tickers))
        hot     = set(scan.hot_tickers(top_n_sectors=2))
        if hot:
            logger.info("Hot tickers (top 2 sectors): %s | sectors: %s",
                        sorted(hot), scan.sector_summary())

        # ── 3. Inject SPY bars into TechnicalAgent for relative-strength ──────
        try:
            spy_bars = await broker.get_bars("SPY", timeframe="5Min", limit=120)
            pm.technical.spy_bars = spy_bars
        except Exception:
            pass

        # ── 4. Evaluate tickers concurrently ──────────────────────────────────
        results = await asyncio.gather(
            *[evaluate_ticker(pm, broker, t, execute=execute) for t in tickers],
            return_exceptions=True,
        )
        for ticker, result in zip(tickers, results):
            if isinstance(result, Exception):
                logger.exception("evaluation failed for %s: %s", ticker, result)
            elif ticker.upper() not in hot:
                logger.info("  %s is in a cold sector — signal noted but deprioritised", ticker)

    await ai4.__aexit__(None, None, None)


if __name__ == "__main__":
    syms = sys.argv[1:] or ["AAPL"]
    asyncio.run(main(syms))

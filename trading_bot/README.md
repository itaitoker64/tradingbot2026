# Multi-Agent Day Trading Bot

A modular, strongly-typed, async multi-agent trading desk. Five agents each
emit a confidence score in **[1, 100]**; a Portfolio Manager blends them,
applies a hard Risk veto, and routes bracket orders to **Alpaca** (backtest /
paper) or **Interactive Brokers** (live).

> ⚠️ **Not financial advice.** This is engineering scaffolding, not a
> profitable strategy and not investment guidance. The author is not your
> advisor. Paper-trade extensively, understand every line, and never run live
> capital you can't afford to lose. You are responsible for compliance with
> each data provider's and broker's Terms of Service.

## Directory structure

```
trading_bot/
├── main.py                     # composition root + run loops
├── requirements.txt
├── .env.example
├── config/
│   └── settings.py             # env-driven Settings, weights, risk, thresholds
├── core/
│   ├── enums.py                # Decision, OrderSide, AgentRole, RunMode
│   ├── models.py               # AnalysisContext, AgentEvaluation, TradeDecision...
│   └── base_agent.py           # BaseAgent ABC -> get_score(ctx) -> int [1..100]
├── data/
│   ├── news_sources.py         # NewsSource ABC + PoliStock stub + Alpaca news
│   └── chart_renderer.py       # OHLCV -> PNG for the Vision agent
├── agents/
│   ├── fundamental_agent.py    # news & sentiment (LLM or lexical fallback)
│   ├── vision_agent.py         # chart-image pattern read via VLM
│   ├── technical_agent.py      # RSI/MACD/EMA/VWAP convergence
│   └── risk_agent.py           # volatility, sizing, SL/TP, VETO
└── execution/
    ├── base_broker.py          # BaseBroker ABC
    ├── alpaca_broker.py        # alpaca-py adapter
    ├── ibkr_broker.py          # ib_insync adapter
    └── portfolio_manager.py    # orchestrator: weight -> decide -> gate -> route
```

## Decision flow

1. `AnalysisContext` is built once per ticker (bars + account + chart).
2. Fundamental, Vision, Technical, Risk run **concurrently** (`asyncio.gather`).
3. The three directional scores are combined into a **confidence-weighted**
   composite in [1, 100].
4. Thresholds map composite → `LONG` / `SHORT` / `PASS`.
5. The Risk agent's **veto** or a sub-floor risk score forces `PASS`.
6. Risk builds a concrete ATR-based plan (qty, stop-loss, take-profit, R/R).
7. The broker submits a bracket order.

Each agent is wrapped in `safe_evaluate`: any exception degrades that agent to
a neutral 50 (logged) instead of crashing the desk.

## Quick start

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # fill in keys

RUN_MODE=backtest python main.py AAPL MSFT
```

The core logic (technical + risk + orchestration) runs **without any broker
SDK or API key** — useful for unit tests. LLM sentiment, vision, Alpaca, and
IBKR activate only when their respective keys/packages are present.

## Important notes on dependencies & data

- **Alpaca:** `alpaca-trade-api` is **deprecated**; this project uses the
  official **`alpaca-py`**. Alpaca has no built-in event-driven backtester —
  it's used here as a historical-data + paper-trading venue. For rigorous
  backtests, feed `get_bars` history into the MAS loop or integrate
  backtrader / vectorbt.
- **IBKR:** uses `ib_insync`. If you hit upstream maintenance issues, the
  community fork `ib_async` is a drop-in (`import ib_async as ib_insync`).
- **PoliStock:** a paid app with **no documented public API**. The
  `PoliStockSource` is a stub that returns nothing until you supply an
  endpoint you are authorised to use. Do **not** scrape content behind its
  auth or in violation of its ToS. `AlpacaNewsSource` is a ready alternative.

## Tuning

Weights, thresholds, and risk parameters are all environment variables — see
`.env.example`. Risk defaults: 1% account risk per trade, min R/R 1.5,
20% max position, ATR(14) × 2 stop / × 3 target.
```
```

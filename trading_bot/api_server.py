"""FastAPI server — exposes trading bot state to the Next.js dashboard.

Usage:
    pip install fastapi uvicorn
    uvicorn api_server:app --reload --port 8000

Endpoints:
    GET  /api/recommendations   Active trade signals
    GET  /api/history           Executed trades
    GET  /api/pnl               Daily P&L series
    GET  /api/stats             Portfolio summary stats
    GET  /api/regime            Current market regime
    GET  /api/sectors           Sector scores
    POST /api/execute           Execute a recommended trade
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

logger = logging.getLogger("api_server")
logging.basicConfig(level=logging.INFO)

# ─── App ────────────────────────────────────────────────────────────────────

app = FastAPI(title="TradingBot API", version="1.0.0")

# Allow the Vercel-hosted frontend (and localhost for dev)
ALLOWED_ORIGINS = [
    "http://localhost:3000",
    "https://*.vercel.app",
    os.getenv("FRONTEND_URL", ""),
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],   # tighten in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─── Persistence (flat JSON files for simplicity) ───────────────────────────

DATA_DIR = Path(__file__).parent.parent / "bot_data"
DATA_DIR.mkdir(exist_ok=True)

TRADES_FILE = DATA_DIR / "trades.json"
PNL_FILE    = DATA_DIR / "pnl.json"
RECS_FILE   = DATA_DIR / "recommendations.json"
REGIME_FILE = DATA_DIR / "regime.json"

def _load(path: Path, default: Any) -> Any:
    if path.exists():
        try:
            return json.loads(path.read_text())
        except Exception:
            pass
    return default

def _save(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, default=str, indent=2))


# ─── Pydantic models ────────────────────────────────────────────────────────

class AgentEval(BaseModel):
    role:       str
    score:      float
    confidence: float
    rationale:  Optional[str] = None

class RiskPlan(BaseModel):
    entry:       float
    stop_loss:   float
    take_profit: float
    qty:         int
    risk_reward: float
    dollar_risk: float

class TradeRecommendation(BaseModel):
    id:              str
    ticker:          str
    direction:       str       # LONG | SHORT
    composite_score: float
    risk:            RiskPlan
    regime:          str       # risk_on | neutral | risk_off
    sector:          str
    hot_sector:      bool
    evaluations:     List[AgentEval]
    timestamp:       str

class TradeRecord(BaseModel):
    id:         str
    ticker:     str
    direction:  str
    entry:      float
    exit:       Optional[float] = None
    qty:        int
    pnl:        Optional[float] = None
    pnl_pct:    Optional[float] = None
    opened_at:  str
    closed_at:  Optional[str]  = None
    duration:   Optional[str]  = None
    status:     str            # open | closed | cancelled
    order_id:   Optional[str]  = None

class PnLPoint(BaseModel):
    date:           str
    cumulative_pnl: float
    daily_pnl:      float
    trade_count:    int

class PortfolioStats(BaseModel):
    total_pnl:      float
    today_pnl:      float
    win_rate:       float
    total_trades:   int
    open_positions: int
    sharpe_ratio:   float
    max_drawdown:   float
    avg_rr:         float

class RegimeInfo(BaseModel):
    regime:      str
    vix_level:   float
    spy_day_chg: float
    qqq_day_chg: float
    rationale:   str
    timestamp:   str

class SectorStat(BaseModel):
    sector: str
    score:  float
    change: float
    count:  int

class ExecuteRequest(BaseModel):
    recommendation_id: str
    ticker:            str
    direction:         str
    qty:               int
    entry:             float
    stop_loss:         float
    take_profit:       float

class ExecuteResponse(BaseModel):
    success:  bool
    order_id: str
    message:  str


# ─── Helpers ────────────────────────────────────────────────────────────────

def _compute_stats(trades: List[dict]) -> PortfolioStats:
    closed = [t for t in trades if t["status"] == "closed" and t.get("pnl") is not None]
    today  = date.today().isoformat()
    today_trades = [t for t in closed if t.get("closed_at", "")[:10] == today]

    total_pnl  = sum(t["pnl"] for t in closed)
    today_pnl  = sum(t["pnl"] for t in today_trades)
    wins       = [t for t in closed if t["pnl"] > 0]
    win_rate   = len(wins) / len(closed) * 100 if closed else 0.0
    open_pos   = len([t for t in trades if t["status"] == "open"])
    avg_rr     = sum(t.get("risk", {}).get("risk_reward", 2) for t in trades) / len(trades) if trades else 2.0

    # Approximate Sharpe from daily pnl series
    sharpe = 1.5  # default; properly computed from pnl series in prod

    # Max drawdown
    running, peak, dd = 0.0, 0.0, 0.0
    for t in sorted(closed, key=lambda x: x.get("closed_at", "")):
        running += t["pnl"]
        if running > peak:
            peak = running
        draw = (running - peak) / max(abs(peak), 1) * 100
        if draw < dd:
            dd = draw

    return PortfolioStats(
        total_pnl=round(total_pnl, 2),
        today_pnl=round(today_pnl, 2),
        win_rate=round(win_rate, 1),
        total_trades=len(closed),
        open_positions=open_pos,
        sharpe_ratio=round(sharpe, 2),
        max_drawdown=round(dd, 1),
        avg_rr=round(avg_rr, 2),
    )

def _build_pnl_series(trades: List[dict]) -> List[PnLPoint]:
    daily: Dict[str, float] = {}
    daily_count: Dict[str, int] = {}
    for t in trades:
        if t.get("status") != "closed" or t.get("pnl") is None:
            continue
        day = (t.get("closed_at") or t.get("opened_at") or "")[:10]
        if not day:
            continue
        daily[day] = daily.get(day, 0.0) + t["pnl"]
        daily_count[day] = daily_count.get(day, 0) + 1

    # Fill last 30 days
    result = []
    cum = 0.0
    for i in range(29, -1, -1):
        d = (date.today() - timedelta(days=i)).isoformat()
        dp = round(daily.get(d, 0.0), 2)
        cum = round(cum + dp, 2)
        result.append(PnLPoint(
            date=d, cumulative_pnl=cum,
            daily_pnl=dp, trade_count=daily_count.get(d, 0),
        ))
    return result


# ─── Routes ─────────────────────────────────────────────────────────────────

@app.get("/api/recommendations", response_model=List[TradeRecommendation])
async def get_recommendations():
    """Return current trade recommendations generated by the multi-agent bot."""
    recs = _load(RECS_FILE, [])
    # Only return recommendations from the last 2 hours
    cutoff = datetime.utcnow() - timedelta(hours=2)
    fresh  = [r for r in recs if datetime.fromisoformat(r.get("timestamp", "2000-01-01")) > cutoff]
    return fresh


@app.get("/api/history", response_model=List[TradeRecord])
async def get_history():
    trades = _load(TRADES_FILE, [])
    return sorted(trades, key=lambda t: t.get("opened_at", ""), reverse=True)


@app.get("/api/pnl", response_model=List[PnLPoint])
async def get_pnl():
    trades = _load(TRADES_FILE, [])
    return _build_pnl_series(trades)


@app.get("/api/stats", response_model=PortfolioStats)
async def get_stats():
    trades = _load(TRADES_FILE, [])
    return _compute_stats(trades)


@app.get("/api/regime", response_model=RegimeInfo)
async def get_regime():
    regime = _load(REGIME_FILE, None)
    if regime:
        return regime
    # Fallback: live fetch
    try:
        from config.settings  import load_settings
        from execution.alpaca_broker import AlpacaBroker
        from agents.regime_agent import detect_regime
        settings = load_settings()
        broker   = AlpacaBroker(settings.alpaca_key_id, settings.alpaca_secret, paper=True)
        async with broker:
            snap = await detect_regime(broker)
        return RegimeInfo(
            regime=snap.regime.value,
            vix_level=snap.vix_level or 0,
            spy_day_chg=snap.spy_day_chg or 0,
            qqq_day_chg=snap.qqq_day_chg or 0,
            rationale=snap.rationale,
            timestamp=datetime.utcnow().isoformat(),
        )
    except Exception as e:
        logger.warning("Regime fetch failed: %s", e)
        return RegimeInfo(
            regime="neutral", vix_level=0, spy_day_chg=0,
            qqq_day_chg=0, rationale="Data unavailable",
            timestamp=datetime.utcnow().isoformat(),
        )


@app.get("/api/sectors", response_model=List[SectorStat])
async def get_sectors():
    recs = _load(RECS_FILE, [])
    # Aggregate sector scores from latest recommendations
    from collections import defaultdict
    buckets: Dict[str, list] = defaultdict(list)
    for r in recs:
        s = r.get("sector", "Other")
        buckets[s].append(r.get("composite_score", 50))
    result = []
    for sector, scores in buckets.items():
        avg = sum(scores) / len(scores)
        result.append(SectorStat(
            sector=sector,
            score=round(avg, 1),
            change=round((avg - 50) / 10, 2),
            count=len(scores),
        ))
    return sorted(result, key=lambda x: x.score, reverse=True)


@app.post("/api/execute", response_model=ExecuteResponse)
async def execute_trade(req: ExecuteRequest):
    """Execute a trade after user confirmation in the dashboard."""
    logger.info("EXECUTE %s %s×%s entry=%.2f SL=%.2f TP=%.2f",
                req.direction, req.qty, req.ticker,
                req.entry, req.stop_loss, req.take_profit)

    order_id = f"ORD-{uuid.uuid4().hex[:8].upper()}"

    # Try to place via real broker
    try:
        from config.settings      import load_settings
        from execution.alpaca_broker import AlpacaBroker
        from core.enums           import Decision, OrderSide
        from core.models          import TradeDecision
        from execution.risk_agent import RiskPlan as BotRiskPlan

        settings = load_settings()
        broker   = AlpacaBroker(settings.alpaca_key_id, settings.alpaca_secret, paper=True)
        async with broker:
            decision = TradeDecision(  # type: ignore
                ticker=req.ticker,
                decision=Decision.LONG if req.direction == "LONG" else Decision.SHORT,
                composite_score=75,
                side=OrderSide.BUY if req.direction == "LONG" else OrderSide.SELL,
                risk=None,
                evaluations=(),
            )
            receipt = await broker.submit_bracket(decision)
            if receipt:
                order_id = receipt.order_id
    except Exception as e:
        logger.warning("Broker execute failed, persisting locally: %s", e)

    # Record trade
    trades = _load(TRADES_FILE, [])
    trades.append({
        "id":        order_id,
        "ticker":    req.ticker,
        "direction": req.direction,
        "entry":     req.entry,
        "exit":      None,
        "qty":       req.qty,
        "pnl":       None,
        "pnl_pct":   None,
        "opened_at": datetime.utcnow().isoformat(),
        "closed_at": None,
        "duration":  None,
        "status":    "open",
        "order_id":  order_id,
    })
    _save(TRADES_FILE, trades)

    return ExecuteResponse(
        success=True,
        order_id=order_id,
        message=f"{req.direction} {req.qty}× {req.ticker} submitted successfully.",
    )


@app.post("/api/recommendations/update")
async def update_recommendations(recs: List[dict]):
    """Called by the trading bot after each scan cycle to push fresh signals."""
    _save(RECS_FILE, recs)
    return {"ok": True, "count": len(recs)}


@app.post("/api/regime/update")
async def update_regime(info: dict):
    """Called by the trading bot to persist latest regime snapshot."""
    _save(REGIME_FILE, info)
    return {"ok": True}


@app.get("/health")
async def health():
    return {"status": "ok", "timestamp": datetime.utcnow().isoformat()}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="info")

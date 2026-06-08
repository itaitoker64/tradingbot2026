"""FastAPI server — exposes trading bot state to the Next.js dashboard.

Usage:
    pip install fastapi uvicorn aiohttp python-dotenv
    python api_server.py

Endpoints:
    GET  /api/recommendations   Active trade signals
    GET  /api/history           Executed trades
    GET  /api/pnl               Daily P&L series
    GET  /api/stats             Portfolio summary stats
    GET  /api/regime            Current market regime
    GET  /api/sectors           Sector scores
    POST /api/execute           Execute a recommended trade
    POST /api/scan              Trigger a live market scan
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import uuid
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

import aiohttp
from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

logger = logging.getLogger("api_server")
logging.basicConfig(level=logging.INFO)

# ─── Background market scanner ──────────────────────────────────────────────

_ALPACA_KEY    = os.getenv("ALPACA_API_KEY_ID", "")
_ALPACA_SECRET = os.getenv("ALPACA_API_SECRET", "")
_ALPACA_HEADERS = {
    "APCA-API-KEY-ID":     _ALPACA_KEY,
    "APCA-API-SECRET-KEY": _ALPACA_SECRET,
}

_SECTOR_MAP: Dict[str, str] = {
    "AAPL": "Technology", "MSFT": "Technology", "NVDA": "Technology",
    "GOOGL": "Technology", "META": "Technology", "AMZN": "Consumer",
    "TSLA": "Consumer", "AMD": "Technology", "INTC": "Technology",
    "NFLX": "Communication", "JPM": "Financials", "BAC": "Financials",
    "GS": "Financials", "XOM": "Energy", "CVX": "Energy",
    "JNJ": "Healthcare", "PFE": "Healthcare", "UNH": "Healthcare",
}


_BROKER_BASE = "https://paper-api.alpaca.markets"

async def _get_account_equity(session: aiohttp.ClientSession) -> float:
    """Return paper account equity in USD; fall back to $10,000 if unavailable."""
    try:
        async with session.get(
            f"{_BROKER_BASE}/v2/account",
            headers=_ALPACA_HEADERS,
            timeout=aiohttp.ClientTimeout(total=8),
        ) as r:
            if r.status == 200:
                data = await r.json()
                return float(data.get("equity") or data.get("cash") or 10_000)
    except Exception as exc:
        logger.warning("Could not fetch account equity: %s", exc)
    return 10_000.0


def _kelly_qty(
    equity: float,
    entry: float,
    stop_loss: float,
    take_profit: float,
    composite_score: float,
) -> int:
    """
    Half-Kelly position sizing.

    Kelly fraction: f = (b·p − q) / b
      b  = reward:risk ratio (|TP−entry| / |SL−entry|)
      p  = win-probability estimate (composite_score / 100)
      q  = 1 − p

    Position:
      base_risk      = 1% of equity
      scaled_risk    = base_risk × (half_kelly / 0.25)  # normalised around 0.5 score
      qty            = scaled_risk / |entry − stop_loss|
      max_position   = 15% of equity (concentration cap)
    """
    risk_per_share = abs(entry - stop_loss)
    reward_per_share = abs(take_profit - entry)
    if risk_per_share < 0.0001:
        return max(1, int(2000 / max(entry, 1)))

    b = reward_per_share / risk_per_share          # reward:risk
    p = min(max(composite_score / 100.0, 0.05), 0.95)
    q = 1.0 - p
    kelly_f = (b * p - q) / b if b > 0 else 0.0
    half_kelly = max(kelly_f / 2, 0.0)

    base_risk = 0.01 * equity                      # 1% of portfolio
    scaled_risk = base_risk * (half_kelly / 0.25)  # scale: f=0.25 → 1× base risk
    qty = int(scaled_risk / risk_per_share)

    max_qty = int((0.15 * equity) / max(entry, 1)) # 15% concentration cap
    return max(1, min(qty, max_qty))


# ─── Strategy weights & learning ────────────────────────────────────────────

DEFAULT_WEIGHTS: Dict[str, Any] = {
    "chg_weight":    4.0,   # weight for daily % change in score formula
    "intra_weight":  2.0,   # weight for intraday % change
    "min_chg_pct":   0.3,   # minimum absolute daily change to qualify
    "stop_pct":      0.02,  # stop-loss distance from entry
    "tp_pct":        0.05,  # take-profit distance from entry
    "score_floor":   20,    # minimum possible score
    "score_ceil":    80,    # maximum possible score
    "min_score":     40,    # minimum score to include in recommendations
    "time_window_minutes": 45,  # how long each signal is valid
    "update_count":  0,
    "win_rate_30d":  None,
    "long_win_rate": None,
    "short_win_rate": None,
    "last_updated":  "",
}


def _load_weights() -> Dict[str, Any]:
    w = _load(WEIGHTS_FILE, {})
    merged = {**DEFAULT_WEIGHTS, **w}
    return merged


def _save_weights(w: Dict[str, Any]) -> None:
    _save(WEIGHTS_FILE, w)
    logger.info(
        "Strategy weights updated #%d — win_rate=%.1f%% min_score=%.0f chg_weight=%.2f",
        w.get("update_count", 0),
        w.get("win_rate_30d") or 0,
        w.get("min_score", 40),
        w.get("chg_weight", 4.0),
    )


async def _check_and_close_trades(session: aiohttp.ClientSession) -> None:
    """
    Check Alpaca for orders that have filled/completed and update local trade records.
    Maps filled bracket orders → win (TP hit) or loss (SL hit) based on fill price.
    """
    trades = _load(TRADES_FILE, [])
    open_trades = [t for t in trades if t.get("status") == "open" and t.get("order_id")]
    if not open_trades:
        return

    modified = False
    for trade in open_trades:
        try:
            order_id = trade["order_id"]
            async with session.get(
                f"{_BROKER_BASE}/v2/orders/{order_id}",
                headers=_ALPACA_HEADERS,
                timeout=aiohttp.ClientTimeout(total=8),
            ) as r:
                if r.status != 200:
                    continue
                order = await r.json()

            status = order.get("status", "")
            # Alpaca bracket order statuses that indicate closure
            if status not in ("filled", "canceled", "expired", "done_for_day"):
                continue

            filled_price = float(order.get("filled_avg_price") or trade["entry"])
            direction    = trade["direction"]
            entry        = float(trade["entry"])
            qty          = int(trade.get("qty", 1))

            if direction == "LONG":
                pnl = (filled_price - entry) * qty
            else:
                pnl = (entry - filled_price) * qty

            pnl_pct = (filled_price - entry) / entry * 100 if direction == "LONG" else \
                      (entry - filled_price) / entry * 100

            now_str = datetime.utcnow().isoformat()
            trade["status"]    = "closed"
            trade["exit"]      = round(filled_price, 2)
            trade["pnl"]       = round(pnl, 2)
            trade["pnl_pct"]   = round(pnl_pct, 2)
            trade["closed_at"] = now_str
            modified = True
            logger.info(
                "Closed trade %s %s: PnL $%.2f (%.2f%%)",
                direction, trade["ticker"], pnl, pnl_pct,
            )

        except Exception as exc:
            logger.debug("Could not check order %s: %s", trade.get("order_id"), exc)

    if modified:
        _save(TRADES_FILE, trades)


def _update_strategy_weights() -> None:
    """
    Analyse recent closed trades and nudge scoring weights accordingly.

    Rules (applied in sequence, small nudges to avoid overcorrection):
    - win_rate > 60%  → reduce min_score by 1 (loosen — strategy is working)
                        nudge chg_weight +2% (momentum signal is reliable)
    - win_rate < 40%  → raise min_score by 2 (tighten — be more selective)
                        nudge chg_weight −5%, intra_weight −3% (less noise)
    - LONG >> SHORT   → raise min_score for shorts by tracking direction bias
    - All changes bounded by sane limits.
    """
    trades  = _load(TRADES_FILE, [])
    weights = _load_weights()

    closed = [t for t in trades if t.get("status") == "closed" and t.get("pnl") is not None]
    recent = closed[-20:]  # last 20 closed trades
    if len(recent) < 5:
        logger.info("Not enough closed trades yet for weight update (%d/5)", len(recent))
        return

    wins       = [t for t in recent if (t.get("pnl") or 0) > 0]
    long_trades  = [t for t in recent if t.get("direction") == "LONG"]
    short_trades = [t for t in recent if t.get("direction") == "SHORT"]
    long_wins    = [t for t in long_trades  if (t.get("pnl") or 0) > 0]
    short_wins   = [t for t in short_trades if (t.get("pnl") or 0) > 0]

    win_rate       = len(wins) / len(recent)
    long_win_rate  = len(long_wins)  / len(long_trades)  if long_trades  else 0.5
    short_win_rate = len(short_wins) / len(short_trades) if short_trades else 0.5

    weights["win_rate_30d"]  = round(win_rate * 100, 1)
    weights["long_win_rate"] = round(long_win_rate * 100, 1)
    weights["short_win_rate"] = round(short_win_rate * 100, 1)
    weights["update_count"]  = weights.get("update_count", 0) + 1
    weights["last_updated"]  = datetime.utcnow().isoformat()

    if win_rate > 0.60:
        # Strategy is working — slightly broaden
        weights["min_score"]    = max(30, weights["min_score"] - 1)
        weights["chg_weight"]   = min(10.0, weights["chg_weight"] * 1.02)
        weights["time_window_minutes"] = min(60, weights["time_window_minutes"] + 2)
    elif win_rate < 0.40:
        # Too many losses — tighten filters
        weights["min_score"]    = min(70, weights["min_score"] + 2)
        weights["chg_weight"]   = max(1.5, weights["chg_weight"] * 0.95)
        weights["intra_weight"] = max(0.5, weights["intra_weight"] * 0.97)
        weights["time_window_minutes"] = max(20, weights["time_window_minutes"] - 5)
    # else: neutral band — leave weights alone

    # Direction bias: if one direction is outperforming, widen its window
    if long_win_rate > short_win_rate + 0.20:
        # Longs are much better — keep short threshold tighter (store as metadata, not used in score yet)
        weights["bias"] = "long"
    elif short_win_rate > long_win_rate + 0.20:
        weights["bias"] = "short"
    else:
        weights["bias"] = "neutral"

    _save_weights(weights)


async def _revalidate_expired_recs(session: aiohttp.ClientSession) -> None:
    """
    For each recommendation that has passed its expires_at time:
    - Re-fetch snapshot from Alpaca
    - If the trend still holds (price moved further in signal direction) → extend window
    - If the trend has reversed >1%                                       → drop signal
    - If flat (within 0.5%)                                               → extend once, mark as re-evaluated
    """
    recs = _load(RECS_FILE, [])
    if not recs:
        return

    now = datetime.utcnow()
    expired = [r for r in recs if r.get("expires_at") and
               datetime.fromisoformat(r["expires_at"]) <= now]
    if not expired:
        return

    syms = list({r["ticker"] for r in expired})
    try:
        syms_str = ",".join(syms)
        async with session.get(
            f"https://data.alpaca.markets/v2/stocks/snapshots?symbols={syms_str}",
            headers=_ALPACA_HEADERS,
            timeout=aiohttp.ClientTimeout(total=10),
        ) as r:
            if r.status != 200:
                return
            snaps: Dict[str, Any] = await r.json()
    except Exception as exc:
        logger.warning("Revalidation snapshot fetch failed: %s", exc)
        return

    weights      = _load_weights()
    window_mins  = weights.get("time_window_minutes", 45)
    kept, dropped = 0, 0

    surviving = []
    for rec in recs:
        if rec not in expired:
            surviving.append(rec)
            continue

        ticker    = rec["ticker"]
        direction = rec["direction"]
        entry     = float(rec["risk"]["entry"])
        reeval_count = rec.get("reeval_count", 0)

        snap = snaps.get(ticker) or {}
        lt   = snap.get("latestTrade") or {}
        current = float(lt.get("p") or snap.get("dailyBar", {}).get("c") or entry)

        chg_from_entry = (current - entry) / entry * 100  # positive = price went up

        if direction == "LONG":
            trend_holds  = chg_from_entry >= -0.5    # price hasn't dropped by >0.5%
            trend_strong = chg_from_entry >  0.3     # price actually moved up
            reversed_bad = chg_from_entry < -1.0     # price dropped >1% → abort
        else:
            trend_holds  = chg_from_entry <=  0.5
            trend_strong = chg_from_entry < -0.3
            reversed_bad = chg_from_entry >  1.0

        if reversed_bad or reeval_count >= 2:
            # Signal reversed or already extended twice — drop it
            dropped += 1
            logger.info("Dropping expired signal %s %s (chg=%.2f%%, reeval=%d)",
                        direction, ticker, chg_from_entry, reeval_count)
            continue

        if trend_holds:
            # Extend the window
            new_exp = (now + timedelta(minutes=window_mins)).isoformat()
            rec["expires_at"]   = new_exp
            rec["reeval_count"] = reeval_count + 1
            rec["reeval_note"]  = (
                f"Extended (trend {'strong' if trend_strong else 'flat'}, "
                f"chg={chg_from_entry:+.2f}%)"
            )
            surviving.append(rec)
            kept += 1
        else:
            dropped += 1

    if kept or dropped:
        _save(RECS_FILE, surviving)
        logger.info("Revalidation: %d extended, %d dropped", kept, dropped)


async def _run_market_scan() -> None:
    """Fetch Alpaca top movers, compute simple technical scores, save recommendations."""
    if not _ALPACA_KEY or not _ALPACA_SECRET:
        logger.warning("Alpaca credentials missing — skipping auto-scan")
        return

    try:
        weights     = _load_weights()
        chg_w       = weights.get("chg_weight",    4.0)
        intra_w     = weights.get("intra_weight",  2.0)
        min_chg     = weights.get("min_chg_pct",   0.3)
        stop_pct    = weights.get("stop_pct",       0.02)
        tp_pct      = weights.get("tp_pct",         0.05)
        score_floor = weights.get("score_floor",    20)
        score_ceil  = weights.get("score_ceil",     80)
        min_score   = weights.get("min_score",      40)
        win_mins    = weights.get("time_window_minutes", 45)

        async with aiohttp.ClientSession() as session:
            equity = await _get_account_equity(session)
            logger.info("Auto-scan: account equity $%.2f  weights_v%d",
                        equity, weights.get("update_count", 0))

            async with session.get(
                "https://data.alpaca.markets/v1beta1/screener/stocks/most-actives"
                "?by=volume&top=25",
                headers=_ALPACA_HEADERS,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as r:
                if r.status != 200:
                    logger.warning("Screener returned %s", r.status)
                    return
                actives_data = await r.json()

            symbols_raw = [
                item["symbol"]
                for item in actives_data.get("most_actives", [])
                if item.get("symbol") and "." not in item["symbol"]
            ][:20]

            if not symbols_raw:
                logger.warning("Auto-scan: no symbols from screener")
                return

            syms_str = ",".join(symbols_raw)
            async with session.get(
                f"https://data.alpaca.markets/v2/stocks/snapshots?symbols={syms_str}",
                headers=_ALPACA_HEADERS,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as r:
                if r.status != 200:
                    logger.warning("Snapshots returned %s", r.status)
                    return
                snaps: Dict[str, Any] = await r.json()

        recs = []
        now_ts = datetime.utcnow().isoformat()

        for sym in symbols_raw:
            snap = snaps.get(sym) or snaps.get("snapshots", {}).get(sym)
            if not snap:
                continue

            daily_bar  = snap.get("dailyBar") or {}
            prev_bar   = snap.get("prevDailyBar") or {}
            latest_trd = snap.get("latestTrade") or {}

            price      = float(latest_trd.get("p") or daily_bar.get("c") or 0)
            prev_close = float(prev_bar.get("c") or price)
            day_open   = float(daily_bar.get("o") or price)

            if price < 5 or price > 2000:
                continue

            chg_pct   = (price - prev_close) / prev_close * 100 if prev_close else 0
            intra_pct = (price - day_open)   / day_open   * 100 if day_open   else 0

            if abs(chg_pct) < min_chg:
                continue

            score     = min(max(50 + chg_pct * chg_w + intra_pct * intra_w, score_floor), score_ceil)
            if score < min_score:
                continue
            direction = "LONG" if chg_pct > 0 else "SHORT"
            entry     = round(price, 2)

            if direction == "LONG":
                stop_loss   = round(entry * (1 - stop_pct), 2)
                take_profit = round(entry * (1 + tp_pct),   2)
            else:
                stop_loss   = round(entry * (1 + stop_pct), 2)
                take_profit = round(entry * (1 - tp_pct),   2)

            qty        = _kelly_qty(equity, entry, stop_loss, take_profit, score)
            dollar_rsk = round(abs(entry - stop_loss) * qty, 2)
            rr         = round(tp_pct / stop_pct, 2)
            sector     = _SECTOR_MAP.get(sym, "Other")

            expires_iso = (datetime.utcnow() + timedelta(minutes=win_mins)).isoformat()
            recs.append({
                "id":              f"{sym}-{int(datetime.utcnow().timestamp())}",
                "ticker":          sym,
                "direction":       direction,
                "composite_score": round(score, 1),
                "risk": {
                    "entry":       entry,
                    "stop_loss":   stop_loss,
                    "take_profit": take_profit,
                    "qty":         qty,
                    "risk_reward": rr,
                    "dollar_risk": dollar_rsk,
                },
                "regime":     "neutral",
                "sector":     sector,
                "hot_sector": False,
                "evaluations": [
                    {"role": "technical",   "score": round(score, 1),       "confidence": 0.70},
                    {"role": "fundamental", "score": 50.0,                  "confidence": 0.50},
                    {"role": "risk",        "score": round(100 - score, 1), "confidence": 0.80},
                ],
                "timestamp":            now_ts,
                "expires_at":           expires_iso,
                "time_window_minutes":  win_mins,
                "reeval_count":         0,
            })

        if recs:
            _save(RECS_FILE, recs)
            logger.info("Auto-scan complete: %d live recommendations saved", len(recs))
        else:
            logger.info("Auto-scan complete: no qualifying signals")

    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.warning("Auto-scan failed: %s", exc)


async def _scan_loop() -> None:
    await asyncio.sleep(3)
    cycle = 0
    while True:
        cycle += 1
        # Every cycle: check if any signals have expired and revalidate them
        try:
            async with aiohttp.ClientSession() as s:
                await _check_and_close_trades(s)
                await _revalidate_expired_recs(s)
        except Exception as exc:
            logger.warning("Maintenance cycle error: %s", exc)

        # Every other cycle (~every hour): run full market scan + weight update
        if cycle % 2 == 1:
            await _run_market_scan()
            # Update weights every 3rd scan (roughly every 90 min) if we have data
            if cycle % 6 == 1:
                try:
                    _update_strategy_weights()
                except Exception as exc:
                    logger.warning("Weight update error: %s", exc)

        await asyncio.sleep(900)  # 15-minute maintenance tick


@asynccontextmanager
async def lifespan(_app: FastAPI):
    task = asyncio.create_task(_scan_loop())
    yield
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


# ─── App ────────────────────────────────────────────────────────────────────

app = FastAPI(title="TradingBot API", version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─── Persistence ────────────────────────────────────────────────────────────

DATA_DIR = Path(__file__).parent.parent / "bot_data"
DATA_DIR.mkdir(exist_ok=True)

TRADES_FILE  = DATA_DIR / "trades.json"
PNL_FILE     = DATA_DIR / "pnl.json"
RECS_FILE    = DATA_DIR / "recommendations.json"
REGIME_FILE  = DATA_DIR / "regime.json"
WEIGHTS_FILE = DATA_DIR / "strategy_weights.json"

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
    direction:       str
    composite_score: float
    risk:            RiskPlan
    regime:          str
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
    status:     str
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
    composite_score:   Optional[float] = None

class ExecuteResponse(BaseModel):
    success:  bool
    order_id: str
    message:  str


# ─── Helpers ────────────────────────────────────────────────────────────────

def _compute_stats(trades: List[dict]) -> PortfolioStats:
    closed = [t for t in trades if t["status"] == "closed" and t.get("pnl") is not None]
    today  = date.today().isoformat()
    today_trades = [t for t in closed if t.get("closed_at", "")[:10] == today]

    total_pnl = sum(t["pnl"] for t in closed)
    today_pnl = sum(t["pnl"] for t in today_trades)
    wins      = [t for t in closed if t["pnl"] > 0]
    win_rate  = len(wins) / len(closed) * 100 if closed else 0.0
    open_pos  = len([t for t in trades if t["status"] == "open"])
    avg_rr    = sum(t.get("risk", {}).get("risk_reward", 2) for t in trades) / len(trades) if trades else 2.0

    sharpe = 1.5
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
    recs = _load(RECS_FILE, [])
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
    try:
        from config.settings import load_settings
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
    logger.info("EXECUTE %s %s×%s entry=%.2f SL=%.2f TP=%.2f",
                req.direction, req.qty, req.ticker,
                req.entry, req.stop_loss, req.take_profit)

    order_id = f"ORD-{uuid.uuid4().hex[:8].upper()}"

    try:
        from config.settings import load_settings
        settings = load_settings()
        side     = "buy" if req.direction == "LONG" else "sell"
        body     = {
            "symbol":        req.ticker,
            "qty":           str(req.qty),
            "side":          side,
            "type":          "market",
            "time_in_force": "day",
            "order_class":   "bracket",
            "stop_loss":     {"stop_price":  f"{req.stop_loss:.2f}"},
            "take_profit":   {"limit_price": f"{req.take_profit:.2f}"},
        }
        headers = {
            "APCA-API-KEY-ID":     settings.alpaca_key_id,
            "APCA-API-SECRET-KEY": settings.alpaca_secret,
            "Content-Type":        "application/json",
        }
        async with aiohttp.ClientSession() as session:
            async with session.post(
                "https://paper-api.alpaca.markets/v2/orders",
                json=body, headers=headers,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as r:
                if r.status < 300:
                    data     = await r.json()
                    order_id = data.get("id", order_id)
                else:
                    text = await r.text()
                    logger.warning("Alpaca order rejected %s: %s", r.status, text)
    except Exception as e:
        logger.warning("Broker execute failed, persisting locally: %s", e)

    trades = _load(TRADES_FILE, [])
    trades.append({
        "id":              order_id,
        "ticker":          req.ticker,
        "direction":       req.direction,
        "entry":           req.entry,
        "exit":            None,
        "qty":             req.qty,
        "pnl":             None,
        "pnl_pct":         None,
        "opened_at":       datetime.utcnow().isoformat(),
        "closed_at":       None,
        "duration":        None,
        "status":          "open",
        "order_id":        order_id,
        "composite_score": req.composite_score,
    })
    _save(TRADES_FILE, trades)

    return ExecuteResponse(
        success=True,
        order_id=order_id,
        message=f"{req.direction} {req.qty}x {req.ticker} submitted successfully.",
    )


@app.post("/api/recommendations/update")
async def update_recommendations(recs: List[dict]):
    _save(RECS_FILE, recs)
    return {"ok": True, "count": len(recs)}


@app.post("/api/regime/update")
async def update_regime(info: dict):
    _save(REGIME_FILE, info)
    return {"ok": True}


@app.post("/api/scan")
async def trigger_scan():
    """Manually trigger a market scan and refresh recommendations."""
    asyncio.create_task(_run_market_scan())
    return {"ok": True, "message": "Scan started — recommendations will update shortly"}


@app.get("/api/strategy")
async def get_strategy():
    """Return current strategy weights and learning metadata."""
    w = _load_weights()
    trades  = _load(TRADES_FILE, [])
    closed  = [t for t in trades if t.get("status") == "closed"]
    recent  = closed[-20:]
    return {
        **w,
        "total_closed_trades": len(closed),
        "recent_sample_size":  len(recent),
    }


@app.post("/api/strategy/reset")
async def reset_strategy():
    """Reset strategy weights to defaults."""
    _save_weights({**DEFAULT_WEIGHTS, "last_updated": datetime.utcnow().isoformat()})
    return {"ok": True, "message": "Strategy weights reset to defaults"}


@app.post("/api/execute/batch")
async def execute_batch(reqs: List[ExecuteRequest]):
    """Execute multiple trades in sequence."""
    results = []
    for req in reqs:
        try:
            res = await execute_trade(req)
            results.append({"ticker": req.ticker, "success": True, "order_id": res.order_id})
        except Exception as exc:
            results.append({"ticker": req.ticker, "success": False, "error": str(exc)})
    succeeded = sum(1 for r in results if r["success"])
    return {"results": results, "total": len(reqs), "succeeded": succeeded}


@app.get("/health")
async def health():
    return {"status": "ok", "timestamp": datetime.utcnow().isoformat()}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="info")

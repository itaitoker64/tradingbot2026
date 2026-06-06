"""Domain enumerations shared across the trading system."""
from __future__ import annotations

from enum import Enum


class Decision(str, Enum):
    """Final directional decision emitted by the Portfolio Manager."""

    LONG = "LONG"
    SHORT = "SHORT"
    PASS = "PASS"


class OrderSide(str, Enum):
    BUY = "buy"
    SELL = "sell"


class OrderType(str, Enum):
    MARKET = "market"
    LIMIT = "limit"
    STOP = "stop"
    BRACKET = "bracket"


class AgentRole(str, Enum):
    FUNDAMENTAL = "fundamental"
    VISION = "vision"
    TECHNICAL = "technical"
    RISK = "risk"


class RunMode(str, Enum):
    """Backtest -> Alpaca paper/historical. Live -> IBKR."""

    BACKTEST = "backtest"
    LIVE = "live"

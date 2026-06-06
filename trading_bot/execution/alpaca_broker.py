"""Alpaca adapter (backtest / paper) built on the official ``alpaca-py`` SDK.

NOTE: ``alpaca-trade-api`` is deprecated; ``alpaca-py`` is the current SDK.
All SDK imports are lazy so this module imports cleanly even when the package
is absent (e.g. in a unit-test environment that only exercises IBKR).

For "backtesting", Alpaca itself has no event-driven backtester: we use it as
a historical data source + paper-trading venue. For a full event loop, feed
``get_bars`` history into the MAS via ``Backtester`` (see main.py) or plug in
a dedicated engine such as backtrader/vectorbt.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Mapping, Optional

import pandas as pd

from core.enums import Decision, OrderSide
from core.models import TradeDecision
from execution.base_broker import BaseBroker, OrderReceipt

logger = logging.getLogger(__name__)


class AlpacaBroker(BaseBroker):
    name = "alpaca"

    def __init__(self, key_id: str, secret: str, *, paper: bool = True) -> None:
        self._key_id = key_id
        self._secret = secret
        self._paper = paper
        self._trading = None
        self._data = None

    async def connect(self) -> None:
        from alpaca.data.historical import StockHistoricalDataClient  # type: ignore
        from alpaca.trading.client import TradingClient  # type: ignore

        self._trading = TradingClient(self._key_id, self._secret, paper=self._paper)
        self._data = StockHistoricalDataClient(self._key_id, self._secret)
        logger.info("Alpaca connected (paper=%s)", self._paper)

    async def disconnect(self) -> None:
        self._trading = None
        self._data = None

    async def get_account(self) -> Mapping[str, float]:
        acct = await asyncio.to_thread(self._trading.get_account)
        return {
            "equity": float(acct.equity),
            "buying_power": float(acct.buying_power),
            "cash": float(acct.cash),
        }

    async def get_bars(self, ticker: str, *, timeframe: str = "1Min", limit: int = 200) -> pd.DataFrame:
        from alpaca.data.requests import StockBarsRequest  # type: ignore
        from alpaca.data.timeframe import TimeFrame, TimeFrameUnit  # type: ignore

        tf_map = {
            "1Min": TimeFrame(1, TimeFrameUnit.Minute),
            "5Min": TimeFrame(5, TimeFrameUnit.Minute),
            "15Min": TimeFrame(15, TimeFrameUnit.Minute),
            "1Hour": TimeFrame(1, TimeFrameUnit.Hour),
            "1Day": TimeFrame(1, TimeFrameUnit.Day),
        }
        req = StockBarsRequest(symbol_or_symbols=ticker, timeframe=tf_map[timeframe], limit=limit)
        bars = await asyncio.to_thread(self._data.get_stock_bars, req)
        df = bars.df
        if df.empty:
            return df
        if isinstance(df.index, pd.MultiIndex):  # (symbol, timestamp)
            df = df.xs(ticker, level=0)
        return df[["open", "high", "low", "close", "volume"]]

    async def submit_bracket(self, decision: TradeDecision) -> Optional[OrderReceipt]:
        if not decision.is_actionable or decision.risk is None:
            return None
        from alpaca.trading.enums import OrderSide as AlpacaSide  # type: ignore
        from alpaca.trading.enums import TimeInForce  # type: ignore
        from alpaca.trading.requests import (  # type: ignore
            LimitOrderRequest,
            StopLossRequest,
            TakeProfitRequest,
        )

        side = AlpacaSide.BUY if decision.side is OrderSide.BUY else AlpacaSide.SELL
        risk = decision.risk
        order = LimitOrderRequest(
            symbol=decision.ticker,
            qty=risk.qty,
            side=side,
            time_in_force=TimeInForce.DAY,
            limit_price=round(risk.entry, 2),
            order_class="bracket",
            take_profit=TakeProfitRequest(limit_price=round(risk.take_profit, 2)),
            stop_loss=StopLossRequest(stop_price=round(risk.stop_loss, 2)),
        )
        submitted = await asyncio.to_thread(self._trading.submit_order, order)
        logger.info("Alpaca bracket submitted %s %s qty=%s", decision.ticker, side, risk.qty)
        return OrderReceipt(
            broker=self.name,
            order_id=str(submitted.id),
            status=str(submitted.status),
            raw={"symbol": decision.ticker},
        )

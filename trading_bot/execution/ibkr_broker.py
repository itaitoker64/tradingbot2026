"""Interactive Brokers adapter (live) using ``ib_insync``.

Requires a running TWS or IB Gateway with the API enabled. ``ib_insync`` is
natively async-friendly (it runs its own event loop integration), so calls
here await its coroutine variants where available.

Heads-up: the original ``ib_insync`` is in maintenance limbo upstream; if you
hit install/compat issues, the community fork ``ib_async`` is a drop-in
replacement (``import ib_async as ib_insync``).
"""
from __future__ import annotations

import logging
from typing import Mapping, Optional

import pandas as pd

from core.enums import OrderSide
from core.models import TradeDecision
from execution.base_broker import BaseBroker, OrderReceipt

logger = logging.getLogger(__name__)


class IBKRBroker(BaseBroker):
    name = "ibkr"

    def __init__(self, host: str = "127.0.0.1", port: int = 7497, client_id: int = 1) -> None:
        self._host = host
        self._port = port
        self._client_id = client_id
        self._ib = None

    async def connect(self) -> None:
        from ib_insync import IB  # type: ignore

        self._ib = IB()
        await self._ib.connectAsync(self._host, self._port, clientId=self._client_id)
        logger.info("IBKR connected %s:%s clientId=%s", self._host, self._port, self._client_id)

    async def disconnect(self) -> None:
        if self._ib is not None:
            self._ib.disconnect()
            self._ib = None

    def _stock(self, ticker: str):
        from ib_insync import Stock  # type: ignore

        return Stock(ticker, "SMART", "USD")

    async def get_account(self) -> Mapping[str, float]:
        summary = await self._ib.accountSummaryAsync()
        wanted = {"NetLiquidation": "equity", "BuyingPower": "buying_power", "TotalCashValue": "cash"}
        out: dict[str, float] = {}
        for row in summary:
            if row.tag in wanted:
                try:
                    out[wanted[row.tag]] = float(row.value)
                except ValueError:
                    continue
        return out

    async def get_bars(self, ticker: str, *, timeframe: str = "1Min", limit: int = 200) -> pd.DataFrame:
        bar_size = {
            "1Min": "1 min",
            "5Min": "5 mins",
            "15Min": "15 mins",
            "1Hour": "1 hour",
            "1Day": "1 day",
        }[timeframe]
        contract = self._stock(ticker)
        await self._ib.qualifyContractsAsync(contract)
        bars = await self._ib.reqHistoricalDataAsync(
            contract,
            endDateTime="",
            durationStr="1 D" if "min" in bar_size else "30 D",
            barSizeSetting=bar_size,
            whatToShow="TRADES",
            useRTH=True,
            formatDate=1,
        )
        if not bars:
            return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
        df = pd.DataFrame(
            [{"date": b.date, "open": b.open, "high": b.high, "low": b.low, "close": b.close, "volume": b.volume} for b in bars]
        ).set_index("date")
        return df.tail(limit)

    async def submit_bracket(self, decision: TradeDecision) -> Optional[OrderReceipt]:
        if not decision.is_actionable or decision.risk is None:
            return None
        contract = self._stock(decision.ticker)
        await self._ib.qualifyContractsAsync(contract)
        action = "BUY" if decision.side is OrderSide.BUY else "SELL"
        risk = decision.risk
        # bracketOrder returns (parent, takeProfit, stopLoss) already linked.
        bracket = self._ib.bracketOrder(
            action,
            int(risk.qty),
            limitPrice=round(risk.entry, 2),
            takeProfitPrice=round(risk.take_profit, 2),
            stopLossPrice=round(risk.stop_loss, 2),
        )
        trade = None
        for order in bracket:
            trade = self._ib.placeOrder(contract, order)
        logger.info("IBKR bracket placed %s %s qty=%s", decision.ticker, action, risk.qty)
        return OrderReceipt(
            broker=self.name,
            order_id=str(trade.order.orderId if trade else "unknown"),
            status="submitted",
            raw={"symbol": decision.ticker},
        )

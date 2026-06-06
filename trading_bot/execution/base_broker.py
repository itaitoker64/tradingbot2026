"""Broker abstraction so the desk is execution-venue agnostic.

The Portfolio Manager depends only on this interface. ``AlpacaBroker`` backs
historical/paper runs; ``IBKRBroker`` backs live execution. Both convert a
``TradeDecision`` into a bracket order (entry + stop-loss + take-profit).
"""
from __future__ import annotations

import abc
from dataclasses import dataclass
from typing import Mapping, Optional

import pandas as pd

from core.models import TradeDecision


@dataclass(slots=True, frozen=True)
class OrderReceipt:
    broker: str
    order_id: str
    status: str
    raw: Mapping[str, object]


class BaseBroker(abc.ABC):
    name: str = "base"

    @abc.abstractmethod
    async def connect(self) -> None: ...

    @abc.abstractmethod
    async def disconnect(self) -> None: ...

    @abc.abstractmethod
    async def get_account(self) -> Mapping[str, float]:
        """Return at least {'equity': float, 'buying_power': float}."""

    @abc.abstractmethod
    async def get_bars(
        self, ticker: str, *, timeframe: str = "1Min", limit: int = 200
    ) -> pd.DataFrame:
        """OHLCV with columns open/high/low/close/volume and a DatetimeIndex."""

    @abc.abstractmethod
    async def submit_bracket(self, decision: TradeDecision) -> Optional[OrderReceipt]:
        ...

    async def __aenter__(self) -> "BaseBroker":
        await self.connect()
        return self

    async def __aexit__(self, *exc) -> None:
        await self.disconnect()

"""News providers for the Fundamental agent.

IMPORTANT: ``PoliStockSource`` is a STUB. PoliStock is a paid product with no
publicly documented API. Do not scrape content behind its authentication or
in violation of its Terms of Service. Implement ``fetch_headlines`` only
against an endpoint you are authorised to use (e.g. an official API key), or
swap in any other licensed news provider that implements ``NewsSource``.
"""
from __future__ import annotations

import abc
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Sequence

import aiohttp

logger = logging.getLogger(__name__)


@dataclass(slots=True, frozen=True)
class Headline:
    ticker: str
    title: str
    summary: str
    published_at: datetime
    url: str = ""
    source: str = ""


class NewsSource(abc.ABC):
    @abc.abstractmethod
    async def fetch_headlines(self, ticker: str, *, limit: int = 20) -> Sequence[Headline]:
        ...


class PoliStockSource(NewsSource):
    """Adapter placeholder. Fill in only with authorised API access."""

    def __init__(self, base_url: str, api_key: str = "", *, timeout_s: float = 10.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = aiohttp.ClientTimeout(total=timeout_s)

    async def fetch_headlines(self, ticker: str, *, limit: int = 20) -> Sequence[Headline]:
        if not self.api_key:
            logger.warning(
                "PoliStockSource has no API key configured; returning no headlines. "
                "Provide an authorised endpoint before using in production."
            )
            return []
        # Replace the path/params/parsing below with the real, authorised API.
        headers = {"Authorization": f"Bearer {self.api_key}"}
        params = {"symbol": ticker, "limit": str(limit)}
        url = f"{self.base_url}/api/news"  # placeholder path
        try:
            async with aiohttp.ClientSession(timeout=self.timeout) as session:
                async with session.get(url, headers=headers, params=params) as resp:
                    resp.raise_for_status()
                    payload = await resp.json()
        except aiohttp.ClientError as exc:
            logger.error("news fetch failed for %s: %s", ticker, exc)
            return []
        return [
            Headline(
                ticker=ticker,
                title=item.get("title", ""),
                summary=item.get("summary", ""),
                published_at=datetime.fromisoformat(item["published_at"]),
                url=item.get("url", ""),
                source="polistock",
            )
            for item in payload.get("items", [])
        ]


class AlpacaNewsSource(NewsSource):
    """Drop-in alternative: Alpaca's news API (available with your data plan).

    Wired lazily so importing this module never requires alpaca-py.
    """

    def __init__(self, key_id: str, secret: str) -> None:
        self._key_id = key_id
        self._secret = secret
        self._client = None

    def _ensure_client(self):
        if self._client is None:
            from alpaca.data.historical.news import NewsClient  # type: ignore

            self._client = NewsClient(self._key_id, self._secret)
        return self._client

    async def fetch_headlines(self, ticker: str, *, limit: int = 20) -> Sequence[Headline]:
        from alpaca.data.requests import NewsRequest  # type: ignore

        client = self._ensure_client()
        req = NewsRequest(symbols=ticker, limit=limit)
        news = client.get_news(req)
        return [
            Headline(
                ticker=ticker,
                title=a.headline,
                summary=a.summary or "",
                published_at=a.created_at,
                url=a.url or "",
                source="alpaca",
            )
            for a in news.data.get("news", [])
        ]

"""Render OHLCV bars to a PNG the Vision agent can read.

Kept dependency-light: uses mplfinance if available, otherwise a plain
matplotlib candlestick-ish line+volume chart. Returns the image path.
"""
from __future__ import annotations

import logging
import tempfile
from pathlib import Path

import pandas as pd

logger = logging.getLogger(__name__)


def render_chart(ticker: str, bars: pd.DataFrame, *, out_dir: str | None = None) -> str | None:
    if bars is None or bars.empty:
        return None
    out = Path(out_dir or tempfile.gettempdir()) / f"{ticker}_chart.png"
    try:
        import mplfinance as mpf  # type: ignore

        mpf.plot(
            bars.rename(columns=str.capitalize),
            type="candle",
            volume=True,
            mav=(9, 21),
            style="charles",
            savefig=dict(fname=str(out), dpi=120, bbox_inches="tight"),
        )
        return str(out)
    except Exception:  # noqa: BLE001
        logger.info("mplfinance unavailable; using matplotlib fallback")

    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(10, 6), height_ratios=[3, 1], sharex=True)
        ax1.plot(bars.index, bars["close"], label="close")
        ax1.plot(bars.index, bars["close"].ewm(span=9).mean(), label="EMA9", alpha=0.7)
        ax1.plot(bars.index, bars["close"].ewm(span=21).mean(), label="EMA21", alpha=0.7)
        ax1.set_title(f"{ticker}")
        ax1.legend(loc="upper left")
        ax2.bar(bars.index, bars["volume"])
        fig.tight_layout()
        fig.savefig(out, dpi=120)
        plt.close(fig)
        return str(out)
    except Exception:  # noqa: BLE001
        logger.exception("chart rendering failed for %s", ticker)
        return None

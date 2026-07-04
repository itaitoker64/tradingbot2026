"""Persistent storage path resolution.

Railway (and most container hosts) give each deploy a fresh, ephemeral
filesystem. Runtime data written to the repo — backtest results, trade
history, recommendations — is therefore wiped on every redeploy. When a
persistent volume is attached, Railway injects RAILWAY_VOLUME_MOUNT_PATH;
writing data there makes it survive deploys.

volume_dir() returns that mount (creating it if needed) or None when no
volume is attached, so callers can fall back to their existing local paths
and keep behaviour identical for local/no-volume runs.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional


def volume_dir() -> Optional[Path]:
    """Persistent volume mount path, or None when not attached.

    PERSIST_DIR overrides (useful for local testing); otherwise Railway's
    auto-injected RAILWAY_VOLUME_MOUNT_PATH is used.
    """
    raw = os.getenv("PERSIST_DIR") or os.getenv("RAILWAY_VOLUME_MOUNT_PATH")
    if not raw:
        return None
    d = Path(raw)
    d.mkdir(parents=True, exist_ok=True)
    return d


def data_dir() -> Path:
    """THE runtime data directory — volume-backed when attached, repo-local otherwise.

    Every reader/writer of runtime state (strategy_weights.json, trade_mode.json,
    broker_mode.json, learning history, ...) must resolve paths through this
    function. Two components resolving the same filename through different rules
    caused a split-brain on Railway: the optimizer wrote tuned params to the
    volume while the RiskAgent read the repo-relative copy and never saw them.
    """
    vol = volume_dir()
    d = (vol / "data") if vol else (Path(__file__).parent.parent / "data")
    d.mkdir(parents=True, exist_ok=True)
    return d

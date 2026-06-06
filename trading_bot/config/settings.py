"""Centralised configuration.

Values are read from environment variables (12-factor style). Copy
``.env.example`` to ``.env`` and load it with python-dotenv in ``main.py``.
Nothing secret is hard-coded.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Mapping

from core.enums import RunMode


def _env(key: str, default: str = "") -> str:
    return os.environ.get(key, default)


def _env_float(key: str, default: float) -> float:
    try:
        return float(os.environ.get(key, default))
    except (TypeError, ValueError):
        return default


@dataclass(slots=True)
class AgentWeights:
    """Relative influence of each analytical agent on the composite score.

    Risk is NOT weighted into the composite; it operates as a multiplier +
    veto in the Portfolio Manager.
    """

    fundamental: float = 0.30
    vision: float = 0.20
    technical: float = 0.50

    def as_map(self) -> Mapping[str, float]:
        total = self.fundamental + self.vision + self.technical
        if total <= 0:
            raise ValueError("agent weights must sum to a positive number")
        return {
            "fundamental": self.fundamental / total,
            "vision": self.vision / total,
            "technical": self.technical / total,
        }


@dataclass(slots=True)
class RiskConfig:
    max_risk_per_trade_pct: float = field(default_factory=lambda: _env_float("MAX_RISK_PER_TRADE_PCT", 0.01))
    min_risk_reward: float = field(default_factory=lambda: _env_float("MIN_RISK_REWARD", 1.5))
    max_position_pct: float = field(default_factory=lambda: _env_float("MAX_POSITION_PCT", 0.20))
    atr_stop_multiple: float = field(default_factory=lambda: _env_float("ATR_STOP_MULTIPLE", 2.0))
    atr_target_multiple: float = field(default_factory=lambda: _env_float("ATR_TARGET_MULTIPLE", 3.0))


@dataclass(slots=True)
class DecisionThresholds:
    long_above: float = field(default_factory=lambda: _env_float("LONG_THRESHOLD", 60.0))
    short_below: float = field(default_factory=lambda: _env_float("SHORT_THRESHOLD", 40.0))
    min_risk_score: float = field(default_factory=lambda: _env_float("MIN_RISK_SCORE", 35.0))


@dataclass(slots=True)
class Settings:
    run_mode: RunMode = field(default_factory=lambda: RunMode(_env("RUN_MODE", "backtest")))

    # Alpaca (backtest / paper)
    alpaca_key_id: str = field(default_factory=lambda: _env("ALPACA_API_KEY_ID"))
    alpaca_secret: str = field(default_factory=lambda: _env("ALPACA_API_SECRET"))
    alpaca_paper: bool = field(default_factory=lambda: _env("ALPACA_PAPER", "true").lower() == "true")

    # IBKR (live)
    ibkr_host: str = field(default_factory=lambda: _env("IBKR_HOST", "127.0.0.1"))
    ibkr_port: int = field(default_factory=lambda: int(_env("IBKR_PORT", "7497")))
    ibkr_client_id: int = field(default_factory=lambda: int(_env("IBKR_CLIENT_ID", "1")))

    # News source (PoliStock or any provider) + LLM for sentiment/vision
    news_base_url: str = field(default_factory=lambda: _env("NEWS_BASE_URL", "https://www.polistock.app/"))
    news_api_key: str = field(default_factory=lambda: _env("NEWS_API_KEY"))
    anthropic_api_key: str = field(default_factory=lambda: _env("ANTHROPIC_API_KEY"))
    llm_model: str = field(default_factory=lambda: _env("LLM_MODEL", "claude-3-5-sonnet-latest"))

    weights: AgentWeights = field(default_factory=AgentWeights)
    risk: RiskConfig = field(default_factory=RiskConfig)
    thresholds: DecisionThresholds = field(default_factory=DecisionThresholds)


def load_settings() -> Settings:
    return Settings()

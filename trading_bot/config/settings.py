"""Centralised configuration — reads from environment / .env file."""
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

def _env_bool(key: str, default: bool = False) -> bool:
    return os.environ.get(key, str(default)).lower() in ("1", "true", "yes")


@dataclass(slots=True)
class AgentWeights:
    """Relative influence of each directional agent on the composite score.

    Risk operates as a veto gate, not part of the blend.
    Set liquid=0 or social=0 to disable those agents.
    """
    fundamental: float = 0.20
    vision:      float = 0.15
    technical:   float = 0.35
    liquid:      float = 0.15   # Liquid positioning/funding
    social:      float = 0.15   # AI4Trade community sentiment

    def as_map(self) -> Mapping[str, float]:
        raw = {
            "fundamental": self.fundamental,
            "vision":      self.vision,
            "technical":   self.technical,
            "liquid":      self.liquid,
            "social":      self.social,
        }
        total = sum(raw.values())
        if total <= 0:
            raise ValueError("agent weights must sum to a positive number")
        return {k: v / total for k, v in raw.items()}


@dataclass(slots=True)
class RiskConfig:
    max_risk_per_trade_pct: float = field(default_factory=lambda: _env_float("MAX_RISK_PER_TRADE_PCT", 0.01))
    min_risk_reward:        float = field(default_factory=lambda: _env_float("MIN_RISK_REWARD", 1.5))
    max_position_pct:       float = field(default_factory=lambda: _env_float("MAX_POSITION_PCT", 0.20))
    atr_stop_multiple:      float = field(default_factory=lambda: _env_float("ATR_STOP_MULTIPLE", 2.0))
    atr_target_multiple:    float = field(default_factory=lambda: _env_float("ATR_TARGET_MULTIPLE", 3.0))


@dataclass(slots=True)
class DecisionThresholds:
    long_above:     float = field(default_factory=lambda: _env_float("LONG_THRESHOLD", 60.0))
    short_below:    float = field(default_factory=lambda: _env_float("SHORT_THRESHOLD", 40.0))
    min_risk_score: float = field(default_factory=lambda: _env_float("MIN_RISK_SCORE", 35.0))


@dataclass(slots=True)
class Settings:
    run_mode: RunMode = field(default_factory=lambda: RunMode(_env("RUN_MODE", "backtest")))

    # Alpaca
    alpaca_key_id: str  = field(default_factory=lambda: _env("ALPACA_API_KEY_ID"))
    alpaca_secret: str  = field(default_factory=lambda: _env("ALPACA_API_SECRET"))
    alpaca_paper:  bool = field(default_factory=lambda: _env_bool("ALPACA_PAPER", True))

    # IBKR
    ibkr_host:      str = field(default_factory=lambda: _env("IBKR_HOST", "127.0.0.1"))
    ibkr_port:      int = field(default_factory=lambda: int(_env("IBKR_PORT", "7497")))
    ibkr_client_id: int = field(default_factory=lambda: int(_env("IBKR_CLIENT_ID", "1")))

    # Liquid broker
    use_liquid_broker: bool = field(default_factory=lambda: _env_bool("USE_LIQUID_BROKER", False))
    liquid_api_key:    str  = field(default_factory=lambda: _env("LIQUID_API_KEY"))

    # AI4Trade social platform
    ai4trade_email:    str  = field(default_factory=lambda: _env("AI4TRADE_EMAIL"))
    ai4trade_password: str  = field(default_factory=lambda: _env("AI4TRADE_PASSWORD"))
    ai4trade_bot_name: str  = field(default_factory=lambda: _env("AI4TRADE_BOT_NAME", "tradingbot2026"))
    ai4trade_publish:  bool = field(default_factory=lambda: _env_bool("AI4TRADE_PUBLISH", True))

    # News + LLM
    news_base_url:     str  = field(default_factory=lambda: _env("NEWS_BASE_URL", "https://www.polistock.app/"))
    news_api_key:      str  = field(default_factory=lambda: _env("NEWS_API_KEY"))
    anthropic_api_key: str  = field(default_factory=lambda: _env("ANTHROPIC_API_KEY"))
    llm_model:         str  = field(default_factory=lambda: _env("LLM_MODEL", "claude-sonnet-4-6"))

    weights:    AgentWeights      = field(default_factory=AgentWeights)
    risk:       RiskConfig        = field(default_factory=RiskConfig)
    thresholds: DecisionThresholds = field(default_factory=DecisionThresholds)


def load_settings() -> Settings:
    return Settings()

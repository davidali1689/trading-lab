"""Weekly improvement scorecard — better / flat / worse per strategy."""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class Trend(StrEnum):
    IMPROVING = "improving"
    FLAT = "flat"
    WORSE = "worse"
    INSUFFICIENT_DATA = "insufficient_data"


class AgentScorecard(BaseModel):
    agent_id: str
    trade_count: int = 0
    skip_count: int = 0
    expectancy_usd: str = "0"
    win_rate: str = "0"
    net_pnl_usd: str = "0"
    max_drawdown_usd: str = "0"
    capture_rate: str = "0"  # 0–1
    gainer_opportunities: int = 0
    gainers_captured: int = 0
    composite: str = "0"
    trend: Trend = Trend.INSUFFICIENT_DATA
    propose_revert: bool = False  # flag only — never auto-applied
    detail: str = ""


class WeeklyScorecard(BaseModel):
    week_id: str
    built_at: str
    prior_week_id: str | None = None
    drawdown_cap_usd: str = "500"
    agents: dict[str, AgentScorecard] = Field(default_factory=dict)
    summary: str = ""

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")

"""Missed-gainer harvest + coach proposal schemas (ops only — never entries)."""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class MissBucket(StrEnum):
    """A = never watched; B = watched/skipped; C = traded but missed the move."""

    NEVER_WATCHLIST = "A_never_watchlist"
    WATCHED_NO_ENTER = "B_watched_no_enter"
    ENTERED_MISSED_MOVE = "C_entered_missed_move"


class MissRecord(BaseModel):
    symbol: str
    percent_change: str | None = None
    price: str | None = None
    volume: str | None = None
    bucket: MissBucket
    owner_sniper: str
    skip_reasons: list[str] = Field(default_factory=list)
    traded_by: list[str] = Field(default_factory=list)
    trade_pnl_pct: str | None = None
    detail: str = ""
    seen_first_hour: bool | None = None
    first_hour_pct: str | None = None


class DailyMissReport(BaseModel):
    """Deterministic postmarket harvest written to S3."""

    day: str
    built_at: str
    top_gainers: list[MissRecord] = Field(default_factory=list)
    per_agent_top_miss: dict[str, MissRecord | None] = Field(default_factory=dict)
    watchlist_symbols: list[str] = Field(default_factory=list)
    traded_symbols: list[str] = Field(default_factory=list)
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


class CoachProposal(BaseModel):
    """Friday coach output — human green-light before overlay apply."""

    week_id: str
    agent_id: str
    built_at: str
    model_id: str
    top_miss: MissRecord | None = None
    analysis: str = ""
    proposed_changes: list[dict[str, Any]] = Field(default_factory=list)
    status: str = "pending_green_light"  # pending_green_light | approved | rejected
    grounding_symbols: list[str] = Field(default_factory=list)
    mock: bool = False

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")

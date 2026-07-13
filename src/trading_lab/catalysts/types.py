"""Catalyst signal types — soft overlays; never force ENTER alone."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal, Protocol

from pydantic import BaseModel, Field


class CatalystKind(StrEnum):
    CONGRESS_TRADE = "congress_trade"


class CatalystSignal(BaseModel):
    kind: CatalystKind
    symbol: str
    direction: Literal["buy", "sell"] | None = None
    disclosed_at: datetime
    transaction_date: datetime | None = None
    source: str = "unusual_whales"
    politician: str = ""
    amounts: str = ""
    meta: dict = Field(default_factory=dict)


class CatalystPort(Protocol):
    def signals_for(self, symbol: str, *, since: datetime) -> list[CatalystSignal]: ...

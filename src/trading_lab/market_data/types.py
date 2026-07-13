"""OHLCV bar types and market-data port."""

from datetime import datetime
from decimal import Decimal
from typing import Protocol

from pydantic import BaseModel, Field


class Bar(BaseModel):
    symbol: str
    ts: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    vwap: Decimal | None = None
    timeframe: str = "1Min"


class BarRequest(BaseModel):
    symbol: str
    timeframe: str
    start: datetime
    end: datetime
    feed: str = "iex"


class MarketDataPort(Protocol):
    def get_bars(self, request: BarRequest) -> list[Bar]: ...


class SessionContext(BaseModel):
    """Inputs available to evaluators at a bar."""

    symbol: str
    bar: Bar
    bars: list[Bar] = Field(default_factory=list)
    rvol: Decimal | None = None
    above_vwap: bool | None = None
    spy_aligned: bool | None = None
    qqq_aligned: bool | None = None
    market_cap_usd: Decimal | None = None
    has_catalyst: bool = False
    rsi: Decimal | None = None
    float_shares: Decimal | None = None

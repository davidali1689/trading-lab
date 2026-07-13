"""Locked market-data vendors for v1.

Primary: Alpaca IEX (paper + historical bars + realtime WS).
Secondary: Finnhub (quotes / backup WS, personal free tier).
All strategy code talks to MarketDataPort — never call vendors directly.
"""

from enum import StrEnum

from pydantic import BaseModel, Field


class VendorId(StrEnum):
    ALPACA = "alpaca"
    FINNHUB = "finnhub"


class DataRole(StrEnum):
    PRIMARY_BARS = "primary_bars"
    PRIMARY_REALTIME = "primary_realtime"
    SECONDARY_QUOTES = "secondary_quotes"
    PAPER_BROKER = "paper_broker"


class VendorLock(BaseModel):
    """Immutable v1 vendor decisions."""

    primary_bars: VendorId = VendorId.ALPACA
    primary_realtime: VendorId = VendorId.ALPACA
    secondary_quotes: VendorId = VendorId.FINNHUB
    paper_broker: VendorId = VendorId.ALPACA
    alpaca_feed: str = Field(
        default="iex",
        description="iex = free realtime; sip = paid fuller tape (later)",
    )
    # Default for sniper/intraday; swing agents override to 1Day in their specs
    bar_timeframe: str = Field(
        default="1Min",
        description="Default bar size; strategy families may override",
    )
    finnhub_ws_symbol_cap: int = Field(
        default=50,
        description="Finnhub free WS symbol limit",
    )


V1_VENDORS = VendorLock()

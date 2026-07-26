"""Locked market-data + catalyst vendors for v1.

Primary bars/realtime/paper: Alpaca IEX.
Secondary quotes: Finnhub.
Soft catalysts (congress): Unusual Whales — never forces ENTER.
All strategy code talks to MarketDataPort / CatalystPort — never call vendors directly.
"""

from enum import StrEnum

from pydantic import BaseModel, Field


class VendorId(StrEnum):
    ALPACA = "alpaca"
    FINNHUB = "finnhub"
    UNUSUAL_WHALES = "unusual_whales"


class VendorLock(BaseModel):
    """Immutable v1 vendor decisions."""

    primary_bars: VendorId = VendorId.ALPACA
    primary_realtime: VendorId = VendorId.ALPACA
    secondary_quotes: VendorId = VendorId.FINNHUB
    paper_broker: VendorId = VendorId.ALPACA
    soft_catalyst: VendorId = VendorId.UNUSUAL_WHALES
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

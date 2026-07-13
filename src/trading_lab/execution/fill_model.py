"""Honest fill model — no mid-bar magic fills."""

from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, Field

from trading_lab.market_data.types import Bar
from trading_lab.schemas.trades import Side


class FillStyle(StrEnum):
    NEXT_BAR_OPEN = "next_bar_open"
    NEXT_BAR_OPEN_SLIPPAGE = "next_bar_open_slippage"


class FillModel(BaseModel):
    """Sim/backtest fills at next bar open (+ optional adverse slippage)."""

    style: FillStyle = FillStyle.NEXT_BAR_OPEN_SLIPPAGE
    slippage_bps: Decimal = Field(
        default=Decimal("5"),
        description="Adverse slippage in basis points (5 = 0.05%)",
    )

    def fill_price(self, signal_bar: Bar, next_bar: Bar, side: Side) -> Decimal:
        px = next_bar.open
        if self.style == FillStyle.NEXT_BAR_OPEN:
            return px
        slip = px * (self.slippage_bps / Decimal("10000"))
        if side == Side.LONG:
            return px + slip  # pay more to buy
        return px - slip  # receive less to sell short

    def exit_fill(self, next_bar: Bar, side: Side, is_stop: bool) -> Decimal:
        """Stops fill worse; targets at next open with same slip direction."""
        px = next_bar.open
        slip = px * (self.slippage_bps / Decimal("10000"))
        if self.style == FillStyle.NEXT_BAR_OPEN:
            return px
        if side == Side.LONG:
            return px - slip if is_stop else px - slip / 2
        return px + slip if is_stop else px + slip / 2


DEFAULT_FILL_MODEL = FillModel()

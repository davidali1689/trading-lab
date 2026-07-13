"""Swing decision card — always includes HoldPlan."""

from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, Field

from trading_lab.agents.swing.momentum import CapTier
from trading_lab.agents.swing.shared_execution import SWING_SHARED
from trading_lab.schemas.hold import HoldPlan
from trading_lab.schemas.trades import Side, TradeIntent


class SwingStatus(StrEnum):
    ENTER = "ENTER"
    WATCH = "WATCH"
    NO_TRADE = "NO_TRADE"


class SwingTradeMap(BaseModel):
    entry_trigger: Decimal
    scale_out_point: Decimal
    final_take_profit: Decimal
    stop_loss: Decimal


class SwingDecision(BaseModel):
    agent_id: str = "swing_momentum"
    symbol: str
    status: SwingStatus
    cap_tier: CapTier
    catalyst: str = ""
    volume_analysis: str = ""
    rvol: Decimal | None = None
    entry_window: str = ""
    trade_map: SwingTradeMap | None = None
    hold_plan: HoldPlan = Field(default_factory=lambda: SWING_SHARED.default_hold_plan)
    side: Side = Side.LONG
    reason: str = ""
    meta: dict = Field(default_factory=dict)

    def to_trade_intent(self, qty: Decimal) -> TradeIntent | None:
        if self.status != SwingStatus.ENTER or self.trade_map is None:
            return None
        return TradeIntent(
            found_by_agent=self.agent_id,
            symbol=self.symbol,
            side=self.side,
            setup_tags=[
                self.agent_id,
                self.status.value,
                "swing",
                self.cap_tier.value,
            ],
            setup_present=True,
            entry_px=self.trade_map.entry_trigger,
            stop_px=self.trade_map.stop_loss,
            target_px=self.trade_map.final_take_profit,
            qty=qty,
            hold_plan=self.hold_plan,
            reason=self.catalyst or self.reason,
            meta={
                **self.meta,
                "found_by_agent": self.agent_id,
                "scale_out_point": str(self.trade_map.scale_out_point),
                "volume_analysis": self.volume_analysis,
                "rvol": str(self.rvol) if self.rvol is not None else None,
                "entry_window": self.entry_window,
                "hold_summary": self.hold_plan.summary,
                "cap_tier": self.cap_tier.value,
            },
        )

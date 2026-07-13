"""Sniper decision card → TradeIntent (always includes HoldPlan)."""

from decimal import Decimal

from pydantic import BaseModel, Field

from trading_lab.agents.sniper.shared_execution import SNIPER_SHARED, SniperStatus
from trading_lab.schemas.hold import HoldPlan
from trading_lab.schemas.trades import Side, TradeIntent


class TradeMap(BaseModel):
    entry_trigger: Decimal
    scale_out_point: Decimal
    final_take_profit: Decimal
    stop_loss: Decimal


class SniperDecision(BaseModel):
    agent_id: str
    symbol: str
    status: SniperStatus
    catalyst: str = ""
    volume_analysis: str = ""
    rvol: Decimal | None = None
    trade_map: TradeMap | None = None
    hold_plan: HoldPlan = Field(default_factory=lambda: SNIPER_SHARED.default_hold_plan)
    side: Side = Side.LONG
    reason: str = ""
    meta: dict = Field(default_factory=dict)

    def to_trade_intent(self, qty: Decimal) -> TradeIntent | None:
        if self.status != SniperStatus.ENTER or self.trade_map is None:
            return None
        return TradeIntent(
            found_by_agent=self.agent_id,
            symbol=self.symbol,
            side=self.side,
            setup_tags=[self.agent_id, self.status.value, "sniper"],
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
                "sniper_status": self.status.value,
                "hold_summary": self.hold_plan.summary,
            },
        )

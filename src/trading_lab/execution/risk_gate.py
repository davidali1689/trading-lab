"""Portfolio risk gate — hard rejects before any fill."""

from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, Field

from trading_lab.schemas.trades import SkipReason, TradeIntent


class RiskGateConfig(BaseModel):
    max_open_positions: int = 3
    max_daily_loss_usd: Decimal = Decimal("500")
    max_position_notional_usd: Decimal = Decimal("10000")
    starting_capital: Decimal = Decimal("100000")


class RiskGateState(BaseModel):
    open_positions: int = 0
    day: date | None = None
    realized_pnl_today: Decimal = Decimal("0")
    last_stop_ts: datetime | None = None
    cooling_off_until: datetime | None = None


class RiskDecision(BaseModel):
    allowed: bool
    skip_reason: SkipReason | None = None
    detail: str = ""


class RiskGate(BaseModel):
    config: RiskGateConfig = Field(default_factory=RiskGateConfig)
    state: RiskGateState = Field(default_factory=RiskGateState)

    def check(self, intent: TradeIntent, now: datetime) -> RiskDecision:
        if (
            self.state.cooling_off_until is not None
            and now < self.state.cooling_off_until
        ):
            return RiskDecision(
                allowed=False,
                skip_reason=SkipReason.COOLING_OFF,
                detail=f"cooling off until {self.state.cooling_off_until.isoformat()}",
            )
        if self.state.open_positions >= self.config.max_open_positions:
            return RiskDecision(
                allowed=False,
                skip_reason=SkipReason.MAX_POSITIONS,
                detail="max open positions reached",
            )
        day = now.date()
        if self.state.day != day:
            self.state.day = day
            self.state.realized_pnl_today = Decimal("0")
        if self.state.realized_pnl_today <= -self.config.max_daily_loss_usd:
            return RiskDecision(
                allowed=False,
                skip_reason=SkipReason.DAILY_LOSS_HIT,
                detail="daily loss limit hit",
            )
        notional = intent.entry_px * intent.qty
        if notional > self.config.max_position_notional_usd:
            return RiskDecision(
                allowed=False,
                skip_reason=SkipReason.RISK_BLOCKED,
                detail="position notional exceeds max",
            )
        return RiskDecision(allowed=True)

    def on_open(self) -> None:
        self.state.open_positions += 1

    def on_close(self, pnl: Decimal, stop_hit: bool, now: datetime, cool_minutes: int = 15) -> None:
        self.state.open_positions = max(0, self.state.open_positions - 1)
        self.state.realized_pnl_today += pnl
        if stop_hit:
            from datetime import timedelta

            self.state.last_stop_ts = now
            self.state.cooling_off_until = now + timedelta(minutes=cool_minutes)

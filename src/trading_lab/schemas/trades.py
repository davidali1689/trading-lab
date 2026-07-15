"""Trade journal schemas — sim/paper/live share the same ledger shape.

found_by_agent is the attribution key for which setup agent found the trade.
"""

from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field, computed_field, model_validator

from trading_lab.schemas.hold import HoldPlan


class RunMode(StrEnum):
    BACKTEST = "backtest"
    SIM = "sim"
    PAPER = "paper"
    LIVE = "live"


class Side(StrEnum):
    LONG = "long"
    SHORT = "short"


class ExitReason(StrEnum):
    TARGET = "target"
    STOP = "stop"
    TIME = "time"
    SIGNAL = "signal"
    EOD = "eod"
    EMA_BREAK = "ema_break"
    MANUAL = "manual"
    RISK_KILL = "risk_kill"
    PDT_EMERGENCY = "pdt_emergency"


class SkipReason(StrEnum):
    SETUP_MISSING = "setup_missing"
    RISK_BLOCKED = "risk_blocked"
    NO_LIQUIDITY = "no_liquidity"
    INSUFFICIENT_BARS = "insufficient_bars"
    OUTSIDE_WINDOW = "outside_window"
    MAX_POSITIONS = "max_positions"
    DAILY_LOSS_HIT = "daily_loss_hit"
    PDT_RESERVE = "pdt_reserve"
    UNSETTLED_FUNDS = "unsettled_funds"
    MARKET_GUARDRAIL = "market_guardrail"
    COOLING_OFF = "cooling_off"
    OTHER = "other"


def _sync_found_by(data: Any) -> Any:
    if not isinstance(data, dict):
        return data
    key = data.get("found_by_agent") or data.get("agent_id")
    if key:
        data["found_by_agent"] = key
        data["agent_id"] = key  # alias for queries / Grafana
    return data


class TradeIntent(BaseModel):
    """Proposal from a setup agent before risk gate."""

    found_by_agent: str = Field(
        ...,
        description="Primary key: which agent found / proposed this setup",
    )
    agent_id: str | None = Field(
        default=None,
        description="Alias of found_by_agent (kept for Grafana filters)",
    )
    symbol: str
    side: Side
    setup_tags: list[str] = Field(default_factory=list)
    setup_present: bool = True
    entry_px: Decimal
    stop_px: Decimal | None = None
    target_px: Decimal | None = None
    qty: Decimal
    hold_plan: HoldPlan
    reason: str = ""
    meta: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def _agent_key(cls, data: Any) -> Any:
        return _sync_found_by(data)


class SkipEvent(BaseModel):
    """Logged when no trade is taken — never force a fill."""

    event_id: UUID
    run_id: UUID
    found_by_agent: str
    agent_id: str | None = None
    symbol: str
    ts: datetime
    mode: RunMode
    skip_reason: SkipReason
    detail: str = ""
    bar_ts: datetime | None = None
    meta: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def _agent_key(cls, data: Any) -> Any:
        return _sync_found_by(data)


class TradeRecord(BaseModel):
    """Closed trade — sim treated as real for P&L accounting."""

    trade_id: UUID
    run_id: UUID
    found_by_agent: str = Field(
        ...,
        description="Primary attribution key — agent that found the trade",
    )
    agent_id: str | None = Field(
        default=None,
        description="Alias of found_by_agent",
    )
    symbol: str
    side: Side
    mode: RunMode
    setup_tags: list[str] = Field(default_factory=list)

    entry_ts: datetime
    entry_px: Decimal
    qty: Decimal
    stop_px: Decimal | None = None
    target_px: Decimal | None = None
    hold_plan: HoldPlan | None = None

    exit_ts: datetime
    exit_px: Decimal
    exit_reason: ExitReason

    fees: Decimal = Decimal("0")
    bars_held: int = 0
    sessions_held: int | None = None
    fill_model: str = "next_bar_open"
    slippage_bps: Decimal = Decimal("0")
    meta: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def _agent_key(cls, data: Any) -> Any:
        return _sync_found_by(data)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def entry_notional(self) -> Decimal:
        return self.entry_px * self.qty

    @computed_field  # type: ignore[prop-decorator]
    @property
    def pnl_usd(self) -> Decimal:
        raw = (self.exit_px - self.entry_px) * self.qty
        if self.side == Side.SHORT:
            raw = -raw
        return raw - self.fees

    @computed_field  # type: ignore[prop-decorator]
    @property
    def pnl_pct(self) -> Decimal:
        notional = self.entry_notional
        if notional == 0:
            return Decimal("0")
        return (self.pnl_usd / notional) * Decimal("100")

    @computed_field  # type: ignore[prop-decorator]
    @property
    def hold_duration_label(self) -> str:
        if self.sessions_held is not None:
            return f"{self.sessions_held} session(s)"
        return f"{self.bars_held} bar(s)"

    @model_validator(mode="after")
    def _exit_after_entry(self) -> "TradeRecord":
        if self.exit_ts < self.entry_ts:
            raise ValueError("exit_ts must be >= entry_ts")
        return self

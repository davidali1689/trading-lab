"""Hold duration — required on every trade intent across all strategies."""

from enum import StrEnum

from pydantic import BaseModel, Field, model_validator


class StrategyHorizon(StrEnum):
    INTRADAY = "intraday"
    SWING = "swing"


class HoldPlan(BaseModel):
    """How long we intend to hold. Mandatory on ENTER."""

    horizon: StrategyHorizon
    min_hold_sessions: int = Field(
        ...,
        ge=0,
        description="Minimum sessions to hold (0 = same day OK; swing uses >=1 overnight)",
    )
    typical_hold_sessions: int = Field(
        ...,
        ge=0,
        description="Expected hold length in sessions (trading days)",
    )
    max_hold_sessions: int = Field(
        ...,
        ge=0,
        description="Hard time stop in sessions if targets not hit",
    )
    summary: str = Field(
        ...,
        min_length=1,
        description="Human-readable hold instruction, always shown with the trade map",
    )

    @model_validator(mode="after")
    def _ordered(self) -> "HoldPlan":
        if not (self.min_hold_sessions <= self.typical_hold_sessions <= self.max_hold_sessions):
            raise ValueError(
                "Require min_hold_sessions <= typical_hold_sessions <= max_hold_sessions"
            )
        if self.horizon == StrategyHorizon.SWING and self.min_hold_sessions < 1:
            raise ValueError("Swing holds require min_hold_sessions >= 1 (overnight)")
        if self.horizon == StrategyHorizon.INTRADAY and self.max_hold_sessions > 0:
            # max 0 sessions means flat by EOD; allow 0/0/0 for pure day trade
            pass
        return self

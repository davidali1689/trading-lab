"""Swing-family shared execution (multi-day). Not used by sniper agents.

What this module does:
- PDT safeguard: min 1 overnight; reserve 3 weekly day-trade bullets for emergencies
- Settled-funds only (T+1) for new entries
- 8% profit ladder (4% scale-out → BE; final 8% / 12% pennies)
- Hard stop + 8-EMA close exit
"""

from decimal import Decimal

from pydantic import BaseModel, Field

from trading_lab.schemas.hold import HoldPlan, StrategyHorizon


class SwingSharedExecution(BaseModel):
    philosophy: str = (
        "Swing momentum with PDT-aware holding. Prefer overnight holds. "
        "Day-trade bullets reserved for catastrophic exits only."
    )

    # PDT / account
    min_overnight_holds: int = 1
    weekly_pdt_bullets_reserved_for_emergency: int = 3
    require_settled_funds: bool = True  # T+1
    target_swing_pct_min: Decimal = Decimal("4")
    target_swing_pct_max: Decimal = Decimal("8")

    # 8% profit ladder
    scale_out_gain_pct: Decimal = Decimal("4")
    scale_out_position_fraction: Decimal = Decimal("0.5")
    move_stop_to_breakeven_on_scale_out: bool = True
    final_target_pct: Decimal = Decimal("8")
    final_target_pct_penny: Decimal = Decimal("12")
    stop_loss_pct: Decimal = Decimal("3")
    stop_loss_pct_penny: Decimal = Decimal("5")
    exit_on_close_below_8ema: bool = True

    default_hold_plan: HoldPlan = Field(
        default_factory=lambda: HoldPlan(
            horizon=StrategyHorizon.SWING,
            min_hold_sessions=1,
            typical_hold_sessions=3,
            max_hold_sessions=10,
            summary=(
                "Swing: hold overnight at least 1 session (PDT bypass). "
                "Typical 2–5 sessions; time-stop by session 10 if targets unmet. "
                "Exit earlier on 8% ladder, stop, or close below 8-EMA."
            ),
        )
    )

    notes: list[str] = Field(
        default_factory=lambda: [
            "Applies to swing_momentum only — not sniper.",
            "Min 1 overnight hold to avoid burning PDT day-trade count.",
            "Reserve the 3 weekly PDT bullets for emergency exits only.",
            "New entries only with settled funds (T+1).",
            "Ladder: sell 50% at +4%, stop→BE; final +8% (+12% penny).",
            "Stop 3% (5% penny) OR immediate exit if daily close < 8-EMA.",
            "Always emit HoldPlan with min/typical/max sessions.",
        ]
    )


SWING_SHARED = SwingSharedExecution()

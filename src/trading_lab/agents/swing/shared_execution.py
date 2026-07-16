"""Swing-family shared execution (multi-day). Not used by sniper agents.

- Prefer multi-session holds (strategy design vs intraday snipers)
- Settled-funds preference for new entries (T+1 cash discipline)
- Profit ladder: scale at +4% → BE; final targets >8% OK on multi-day rallies
- Hard stop + 8-EMA close exit
- Live margin: ~$2k minimum equity for leverage; broker intraday margin applies
"""

from decimal import Decimal

from pydantic import BaseModel, Field

from trading_lab.schemas.hold import HoldPlan, StrategyHorizon


class SwingSharedExecution(BaseModel):
    philosophy: str = (
        "Swing momentum: prefer multi-session holds by design. "
        "Let multi-day rallies run past 8%. "
        "Same-day exits allowed on stop / 8-EMA break / risk kill. "
        "Never force a trade. Size = one budget slice (equity/5)."
    )

    # Hold / account discipline (strategy)
    min_overnight_holds: int = 1
    prefer_multi_session_hold: bool = True
    require_settled_funds: bool = True  # T+1 cash discipline for new entries
    margin_min_equity_usd: Decimal = Decimal("2000")
    target_swing_pct_min: Decimal = Decimal("8")
    target_swing_pct_max: Decimal = Decimal("20")

    # Profit ladder — scale early, final target allows multi-day extension
    scale_out_gain_pct: Decimal = Decimal("4")
    scale_out_position_fraction: Decimal = Decimal("0.5")
    move_stop_to_breakeven_on_scale_out: bool = True
    final_target_pct: Decimal = Decimal("12")  # multi-day OK >8%
    final_target_pct_penny: Decimal = Decimal("20")
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
                "Swing: prefer ≥1 overnight session (multi-day setup). "
                "Typical 2–5 sessions; time-stop by session 10 if targets unmet. "
                "Scale 50% at +4% → BE; final ~12% (pennies ~20%) — >8% OK on rallies. "
                "Exit earlier on stop or close below 8-EMA."
            ),
        )
    )

    notes: list[str] = Field(
        default_factory=lambda: [
            "Applies to swing_momentum only — not sniper.",
            "Never force a trade — NO_TRADE/SKIP is success when setup missing (Douglas).",
            "Budget: one slice = equity/5; max 3 open positions across the book.",
            "Multi-day rallies may exceed 8% — final target 12% (20% micro/penny).",
            "Scale 50% at +4%, stop→BE; trail remainder to final or 8-EMA exit.",
            "Broker intraday margin monitors exposure; ~$2k min equity for leverage.",
            "Min 1 overnight is strategy preference (swing vs sniper).",
            "New entries prefer settled funds (T+1 cash discipline).",
            "Stop 3% (5% penny) OR exit if daily close < 8-EMA.",
            "Always emit HoldPlan with min/typical/max sessions.",
        ]
    )


SWING_SHARED = SwingSharedExecution()

"""Common rules shared by ALL strategy families (sniper + swing).

This is NOT sniper scale-out / cooling-off — those live under agents/sniper/.
"""

from pydantic import BaseModel, Field


class CommonExecutionRules(BaseModel):
    """Cross-strategy invariants."""

    never_force_trade: bool = True
    require_hold_plan_on_enter: bool = True
    log_skips: bool = True
    notes: list[str] = Field(
        default_factory=lambda: [
            "Never force a trade — NO_TRADE / SKIP is a successful outcome (Douglas).",
            "Every ENTER must include HoldPlan (min / typical / max + summary).",
            "Budget: current platform equity ÷ 5 (dynamic); 1 slice/agent; ≤3 open.",
            "Unused slices stay cash — selectivity over activity (Minervini / Livermore).",
            "Sim fills use the same P&L ledger as paper/live (Elder journal discipline).",
            "Strategy-family rules (sniper cooling-off, swing multi-session hold) are NOT here.",
        ]
    )


COMMON_EXECUTION = CommonExecutionRules()

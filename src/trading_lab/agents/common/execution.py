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
            "NO_TRADE / skip is a successful outcome when setup is missing.",
            "Every ENTER must include HoldPlan (min / typical / max + summary).",
            "Sim fills use the same P&L ledger as paper/live.",
            "Strategy-family rules (sniper cooling-off, swing PDT) are NOT here.",
        ]
    )


COMMON_EXECUTION = CommonExecutionRules()

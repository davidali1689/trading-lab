"""How we keep improving agents — metrics loops vs LLM tooling.

Rule agents (sniper/swing gates) improve from walk-forward journals, not from
prompt chat. LLM tooling (Langfuse, etc.) is for the optional coach layer only.
"""

from pydantic import BaseModel, Field


class ImprovementStack(BaseModel):
    """Recommended tooling by layer."""

    rule_eval_loop: list[str] = Field(
        default_factory=lambda: [
            "walk_forward_bakeoff on fixed bar windows",
            "rank by expectancy_usd, false_setup_rate, max_drawdown",
            "promote/kill agents from AgentAccuracyReport",
            "Cursor /loop weekly: re-run bake-off + diff reports",
            "Optional: Optuna/grid search on RVOL, stop, target within bounds",
        ]
    )
    llm_coach_optional: list[str] = Field(
        default_factory=lambda: [
            "Langfuse: trace coach prompts, scores, latency (not entry decisions)",
            "EOD Bedrock post-mortem: digest journal + narrative (MOCK_BEDROCK default)",
            "Never let Langfuse/LLM override risk gate or ENTER gates",
        ]
    )
    do_not_use_for_entries: list[str] = Field(
        default_factory=lambda: [
            "LangGraph/Crew multi-agent debate for entries",
            "Autonomous prompt loops that change stops without bake-off",
        ]
    )
    notes: list[str] = Field(
        default_factory=lambda: [
            "found_by_agent is the attribution key for every trade/skip.",
            "Improvement signal = journal metrics by found_by_agent, not chat vibes.",
            "Langfuse is observability for LLM coach — free self-host or cloud free tier.",
            "Cursor loops are good for scheduled bake-offs in this workspace.",
        ]
    )


IMPROVEMENT = ImprovementStack()

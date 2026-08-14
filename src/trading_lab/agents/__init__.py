"""Multi-strategy agent registry: sniper (intraday) + swing."""

from typing import Any

from trading_lab.agents.common.execution import COMMON_EXECUTION
from trading_lab.agents.sniper import (
    GAINER_SNIPER,
    LARGE_CAP_SNIPER,
    MID_CAP_SNIPER,
    SNIPER_SHARED,
    SPECULATIVE_SNIPER,
)
from trading_lab.agents.swing import SWING_MOMENTUM, SWING_SHARED

AgentSpec = (
    type(LARGE_CAP_SNIPER)
    | type(MID_CAP_SNIPER)
    | type(SPECULATIVE_SNIPER)
    | type(GAINER_SNIPER)
    | type(SWING_MOMENTUM)
)

AGENTS: dict[str, Any] = {
    LARGE_CAP_SNIPER.agent_id: LARGE_CAP_SNIPER,
    MID_CAP_SNIPER.agent_id: MID_CAP_SNIPER,
    SPECULATIVE_SNIPER.agent_id: SPECULATIVE_SNIPER,
    GAINER_SNIPER.agent_id: GAINER_SNIPER,
    SWING_MOMENTUM.agent_id: SWING_MOMENTUM,
}


def get_agent(agent_id: str) -> Any:
    try:
        return AGENTS[agent_id]
    except KeyError as exc:
        known = ", ".join(sorted(AGENTS))
        raise KeyError(f"Unknown agent_id={agent_id!r}. Known: {known}") from exc


def all_agent_notes() -> dict[str, list[str]]:
    return {
        "common_execution": list(COMMON_EXECUTION.notes),
        "sniper_shared_execution": list(SNIPER_SHARED.notes),
        "swing_shared_execution": list(SWING_SHARED.notes),
        LARGE_CAP_SNIPER.agent_id: list(LARGE_CAP_SNIPER.notes),
        MID_CAP_SNIPER.agent_id: list(MID_CAP_SNIPER.notes),
        SPECULATIVE_SNIPER.agent_id: list(SPECULATIVE_SNIPER.notes),
        GAINER_SNIPER.agent_id: list(GAINER_SNIPER.notes),
        SWING_MOMENTUM.agent_id: list(SWING_MOMENTUM.notes),
    }


__all__ = [
    "AGENTS",
    "COMMON_EXECUTION",
    "get_agent",
    "all_agent_notes",
]

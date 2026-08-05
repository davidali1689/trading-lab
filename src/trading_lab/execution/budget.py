"""Dynamic budget from platform equity: equity ÷ 5 slices; trade with ≤3.

Each agent entry sizes to one slice of *current* account equity (Alpaca paper
or live). As equity compounds or shrinks, slice size moves with it — never a
hardcoded dollar budget.

Speculative is half-sized (equity ÷ 10) after 2026-08-05 drawdowns on full slices.
"""

from __future__ import annotations

from decimal import ROUND_DOWN, Decimal

from trading_lab.execution.risk_gate import RiskGateConfig

BUDGET_SLICES = 5
ACTIVE_SLICES = 3  # max concurrent positions (= trade with 3 of 5)
SPECULATIVE_BUDGET_SLICES = 10  # half of a standard slice
SPECULATIVE_AGENT_ID = "speculative_sniper"


def slice_notional(equity: Decimal) -> Decimal:
    """Standard per-agent buying power: 1/5 of current equity."""
    if equity <= 0:
        return Decimal("0")
    return (equity / Decimal(BUDGET_SLICES)).quantize(Decimal("0.01"), rounding=ROUND_DOWN)


def agent_slice_notional(equity: Decimal, agent_id: str) -> Decimal:
    """Per-agent notional — speculative uses half of a standard slice."""
    if equity <= 0:
        return Decimal("0")
    if agent_id == SPECULATIVE_AGENT_ID:
        return (equity / Decimal(SPECULATIVE_BUDGET_SLICES)).quantize(
            Decimal("0.01"), rounding=ROUND_DOWN
        )
    return slice_notional(equity)


def risk_config_from_equity(equity: Decimal) -> RiskGateConfig:
    """Risk gate tied to current equity slices — no hardcoded $ budget."""
    if equity <= 0:
        raise ValueError("equity must be positive to build risk config")
    one = slice_notional(equity)
    return RiskGateConfig(
        max_open_positions=ACTIVE_SLICES,
        max_position_notional_usd=one,
        # One full slice lost in a day → stop new ENTERs.
        max_daily_loss_usd=one,
        starting_capital=equity,
    )

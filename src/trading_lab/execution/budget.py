"""Dynamic budget from platform equity: equity ÷ 5 slices; trade with ≤3.

Each agent entry sizes to one slice of *current* account equity (Alpaca paper
or live). As equity compounds or shrinks, slice size moves with it — never a
hardcoded dollar budget.
"""

from __future__ import annotations

from decimal import ROUND_DOWN, Decimal

from trading_lab.execution.risk_gate import RiskGateConfig

BUDGET_SLICES = 5
ACTIVE_SLICES = 3  # max concurrent positions (= trade with 3 of 5)


def slice_notional(equity: Decimal) -> Decimal:
    """Per-agent buying power: 1/5 of current equity."""
    if equity <= 0:
        return Decimal("0")
    return (equity / Decimal(BUDGET_SLICES)).quantize(Decimal("0.01"), rounding=ROUND_DOWN)


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

"""Dynamic budget from platform equity: equity ÷ 5 slices; trade with ≤3.

Each agent entry sizes to one slice of *current* account equity (Alpaca paper
or live). As equity compounds or shrinks, slice size moves with it — never a
hardcoded dollar budget.

Speculative is half-sized (equity ÷ 10) after 2026-08-05 drawdowns on full slices.
"""

from __future__ import annotations

import os
from decimal import ROUND_DOWN, Decimal

from trading_lab.execution.risk_gate import RiskGateConfig

BUDGET_SLICES = 5
ACTIVE_SLICES = 3  # max concurrent positions (= trade with 3 of 5)
SPECULATIVE_BUDGET_SLICES = 10  # half of a standard slice
SPECULATIVE_AGENT_ID = "speculative_sniper"

# 2026-08-11: avg stop −$495 vs avg winner +$121 post-remediation — cap what one
# stop can lose so a single loser can't erase ~4 winners.
DEFAULT_MAX_TRADE_RISK_PCT = Decimal("0.25")


def max_trade_risk_pct() -> Decimal:
    """Percent of current equity one stop-out may lose. 0 disables the cap."""
    raw = os.environ.get("MAX_TRADE_RISK_PCT", str(DEFAULT_MAX_TRADE_RISK_PCT))
    try:
        val = Decimal(raw)
    except Exception:  # noqa: BLE001
        return DEFAULT_MAX_TRADE_RISK_PCT
    return val if val >= 0 else DEFAULT_MAX_TRADE_RISK_PCT


def cap_qty_by_risk(
    *,
    entry_px: Decimal,
    stop_px: Decimal | None,
    qty: Decimal,
    equity: Decimal,
) -> Decimal:
    """Shrink qty so (entry − stop) × qty ≤ equity × MAX_TRADE_RISK_PCT / 100.

    Returns 0 when even one share exceeds the cap (caller skips — never force).
    Unpriceable risk (no stop / inverted stop) passes through unchanged: the
    bracket-leg fail-safe already guards naked positions.
    """
    pct = max_trade_risk_pct()
    if pct <= 0 or stop_px is None:
        return qty
    per_share = entry_px - stop_px
    if per_share <= 0:
        return qty
    cap_usd = equity * pct / Decimal("100")
    max_qty = (cap_usd / per_share).to_integral_value(rounding=ROUND_DOWN)
    if max_qty < 1:
        return Decimal("0")
    return min(qty, max_qty)


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

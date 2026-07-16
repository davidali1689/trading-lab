"""Live paper tick — back-compat wrapper around multi-agent sniper path.

Prefer `run_symbol_paper_tick` for full routing (speculative + large + swing).
"""

from __future__ import annotations

from decimal import Decimal

from trading_lab.agents.sniper.large_cap import LARGE_CAP_SNIPER
from trading_lab.pipeline.paper_agents import run_sniper_paper_tick
from trading_lab.pipeline.paper_submit import qty_for_price

# Re-export for existing tests
_qty_for_price = qty_for_price


def run_paper_tick(
    *,
    symbol: str,
    journal_path: str,
    market_cap_usd: Decimal = Decimal("3000000000000"),
    notional_usd: Decimal | None = None,
) -> dict:
    """Evaluate large_cap_sniper on latest bars and submit paper bracket if ENTER."""
    return run_sniper_paper_tick(
        symbol=symbol,
        journal_path=journal_path,
        agent_id=LARGE_CAP_SNIPER.agent_id,
        market_cap_usd=market_cap_usd,
        notional_usd=notional_usd,
    )

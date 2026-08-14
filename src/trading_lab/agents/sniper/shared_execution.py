"""Sniper-family shared execution (intraday only).

What this module does:
- Scale-out at 50% of target → sell half, stop to breakeven
- 15-minute cooling-off after a stop-loss (anti-revenge)
- Prefer HVN breakout into LVN
- ENTER / WATCH / NO_TRADE vocabulary

It does NOT apply to swing agents. Cross-strategy rules live in agents.common.
"""

from datetime import datetime, timedelta
from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, Field

from trading_lab.schemas.hold import HoldPlan, StrategyHorizon


class SniperStatus(StrEnum):
    ENTER = "ENTER"
    WATCH = "WATCH"
    NO_TRADE = "NO_TRADE"


class SniperSharedExecution(BaseModel):
    philosophy: str = (
        "High-conviction, low-frequency intraday. Prefer NO_TRADE in chop. "
        "Never force a trade. Cooling-off after stop-outs is mandatory. "
        "Size = one budget slice (equity/5)."
    )
    scale_out_fraction_of_target: Decimal = Decimal("0.5")
    scale_out_position_fraction: Decimal = Decimal("0.5")
    move_stop_to_breakeven_on_scale_out: bool = True
    cooling_off_after_stop: timedelta = timedelta(minutes=15)
    # Deferred: volume-profile HVN→LVN is noisy on IEX; enable later.
    require_hvn_break_into_lvn: bool = False
    hvn_lvn_deferred: bool = True
    no_trade_in_choppy_market: bool = True
    # Flat by close — intraday
    default_hold_plan: HoldPlan = Field(
        default_factory=lambda: HoldPlan(
            horizon=StrategyHorizon.INTRADAY,
            min_hold_sessions=0,
            typical_hold_sessions=0,
            max_hold_sessions=0,
            summary="Intraday only — flat by regular-session close; no overnight.",
        )
    )
    notes: list[str] = Field(
        default_factory=lambda: [
            "Applies to large_cap_sniper + mid_cap_sniper + speculative_sniper + gainer_sniper.",
            "Never force a trade — chop → NO_TRADE (Douglas / Livermore).",
            "Budget: one slice = equity/5; book max 3 open positions.",
            "At 50% of target: sell 50%, stop → breakeven on remainder (Elder money mgmt).",
            "After stop-loss: block new ENTERs for 15 minutes (anti-revenge).",
            "HVN→LVN deferred (require_hvn_break_into_lvn=False in v0).",
            "Hold: always intraday / flat by EOD (max_hold_sessions=0).",
        ]
    )


SNIPER_SHARED = SniperSharedExecution()


def scale_out_price(entry: Decimal, take_profit: Decimal, side: str = "long") -> Decimal:
    half = SNIPER_SHARED.scale_out_fraction_of_target
    if side == "short":
        return entry - (entry - take_profit) * half
    return entry + (take_profit - entry) * half


def in_cooling_off(
    last_stop_ts: datetime | None,
    now: datetime,
    rules: SniperSharedExecution = SNIPER_SHARED,
) -> bool:
    if last_stop_ts is None:
        return False
    return now < last_stop_ts + rules.cooling_off_after_stop

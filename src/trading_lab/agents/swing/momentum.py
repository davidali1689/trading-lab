"""#FULL_SPECTRUM swing momentum — multi-cap filters + entry timing.

Source: user's swing strategy (multi-session hold, 8-EMA, RVOL tiers,
Power Hour / 10AM sniper).
"""

from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, Field

from trading_lab.agents.swing.shared_execution import SWING_SHARED, SwingSharedExecution
from trading_lab.schemas.hold import HoldPlan


class CapTier(StrEnum):
    LARGE = "large"  # >$10B
    MID = "mid"  # $2B–$10B
    MICRO = "micro"  # <$2B


class SwingMomentumSpec(BaseModel):
    agent_id: str = "swing_momentum"
    family: str = "swing"
    mode_name: str = "Full-Spectrum Swing Momentum"
    source: str = "Swing strategy — #FULL_SPECTRUM + entry/risk rules"
    bar_timeframe: str = "1Day"

    # Market / trend gates
    require_spy_or_qqq_above_20dma: bool = True
    require_price_above_8ema: bool = True

    # RVOL by cap (breakout fuel)
    large_cap_min_usd: Decimal = Decimal("10000000000")
    mid_cap_min_usd: Decimal = Decimal("2000000000")
    large_rvol_min: Decimal = Decimal("1.25")  # 125%+
    mid_rvol_min: Decimal = Decimal("1.50")  # 150%+
    mid_require_rs_vs_spy_qqq: bool = True
    micro_rvol_min: Decimal = Decimal("2.00")  # 200%+

    # Sector sympathy
    sector_sympathy_min_peers: int = 2
    outlier_catalyst_rvol: Decimal = Decimal("5.00")  # RVOL >500% scan flag

    # Entry timing
    prefer_power_hour_et: str = "15:30"  # 3:30 PM EST
    ten_am_sniper_enabled: bool = True
    # 10AM: stock green while SPY/QQQ red

    # Unusual Whales congress soft catalyst (off until API subscription)
    congress_catalyst_enabled: bool = False
    congress_lookback_days: int = 30
    congress_soft_only: bool = True
    congress_source: str = "unusual_whales"

    shared: SwingSharedExecution = Field(default_factory=lambda: SWING_SHARED)

    notes: list[str] = Field(
        default_factory=lambda: [
            "Swing longs only if SPY or QQQ > 20-day MA (market direction filter — O'Neil).",
            "Entry valid only if ticker > 8-day EMA (trend confirmation — Murphy).",
            "RVOL: large ≥125%, mid ≥150%+RS vs SPY/QQQ, micro ≥200% (relative strength).",
            "Never force a trade — congress/catalyst never upgrades NO_TRADE→ENTER.",
            "Budget: sizes to 1/5 equity; book max 3 positions.",
            "Multi-day rallies: final target ~12% (20% micro) — >8% OK; scale at +4%.",
            "Prefer Power Hour (15:30 ET) for strong daily close confirmation.",
            "10AM sniper: enter only if stock green while SPY/QQQ red.",
            "Hold: min 1 overnight; typical ~3 sessions; max 10 (see HoldPlan).",
            "Sector sympathy: if 2+ peers hit volume, scan laggards.",
            "Unusual Whales congress: DISABLED by default (needs API sub).",
        ]
    )

    def rvol_gate(self, market_cap_usd: Decimal) -> Decimal:
        if market_cap_usd >= self.large_cap_min_usd:
            return self.large_rvol_min
        if market_cap_usd >= self.mid_cap_min_usd:
            return self.mid_rvol_min
        return self.micro_rvol_min

    def hold_plan_for_tier(self, tier: CapTier) -> HoldPlan:
        base = self.shared.default_hold_plan
        if tier == CapTier.MICRO:
            return base.model_copy(
                update={
                    "typical_hold_sessions": 2,
                    "max_hold_sessions": 7,
                    "summary": (
                        "Swing micro/penny: prefer ≥1 overnight (multi-session). "
                        "Typical 2–4 sessions; time-stop by session 7. "
                        "Ladder +20% final / 5% stop; exit if close < 8-EMA."
                    ),
                }
            )
        if tier == CapTier.MID:
            return base.model_copy(
                update={
                    "typical_hold_sessions": 4,
                    "summary": (
                        "Swing mid-cap: prefer ≥1 overnight (multi-session). "
                        "Typical 3–5 sessions; time-stop by session 10. "
                        "Ladder +12% final / 3% stop (>8% OK on multi-day); exit if close < 8-EMA."
                    ),
                }
            )
        return base


SWING_MOMENTUM = SwingMomentumSpec()

"""Mode A — Large-Cap Sniper (intraday). From Sniper Trading Strategist v2.0."""

from decimal import Decimal

from pydantic import BaseModel, Field

from trading_lab.agents.sniper.shared_execution import SNIPER_SHARED, SniperSharedExecution


class LargeCapSniperSpec(BaseModel):
    agent_id: str = "large_cap_sniper"
    family: str = "sniper"
    mode_name: str = "Large-Cap Sniper"
    source: str = "Sniper Trading Strategist v2.0 — Mode A"

    min_market_cap_usd: Decimal = Decimal("10000000000")
    profit_target_pct_min: Decimal = Decimal("3.0")
    profit_target_pct_max: Decimal = Decimal("4.0")
    stop_loss_pct_min: Decimal = Decimal("1.5")
    stop_loss_pct_max: Decimal = Decimal("2.0")
    default_profit_target_pct: Decimal = Decimal("3.5")
    default_stop_loss_pct: Decimal = Decimal("1.75")
    bar_timeframe: str = "1Min"
    min_rvol: Decimal = Decimal("1.5")
    # Paper eval needs reachable setups; live keeps the strict floor.
    min_rvol_paper: Decimal = Decimal("1.25")
    require_catalyst: bool = True
    require_catalyst_in_backtest: bool = False  # news sparse historically
    require_catalyst_in_paper: bool = True  # Finnhub company-news on paper path
    require_price_above_vwap: bool = True
    require_aligned_with_spy_qqq: bool = True
    catalyst_types: list[str] = Field(
        default_factory=lambda: [
            "earnings",
            "analyst_upgrade",
            "sector_momentum",
            "other_significant_news",
        ]
    )
    shared: SniperSharedExecution = Field(default_factory=lambda: SNIPER_SHARED)
    notes: list[str] = Field(
        default_factory=lambda: [
            "Intraday large-cap leaders ≥$10B — trade with the tape (Livermore / Murphy).",
            "Gates: RVOL>1.5; above VWAP; SPY/QQQ aligned; catalyst (relaxed paper/backtest).",
            "Target 3–4%; stop 1.5–2%. Flat by EOD.",
            "Never force a trade — missing gates → SKIP (Douglas).",
            "Budget: sizes to 1/5 equity; book max 3 positions.",
            "Define risk before entry; 15m cool-off after stop (Elder / Tharp).",
            "Attribution: found_by_agent=large_cap_sniper.",
        ]
    )


LARGE_CAP_SNIPER = LargeCapSniperSpec()

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
    require_catalyst: bool = True
    require_catalyst_in_backtest: bool = False  # news sparse historically
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
            "Intraday large-cap only — market leaders >$10B.",
            "RVOL >1.5; catalyst; above VWAP; SPY/QQQ aligned.",
            "Target 3–4%; stop 1.5–2%. Hold: flat by EOD (no overnight).",
            "Uses sniper shared_execution (scale-out, 15m cool-off).",
            "HVN/LVN deferred — not a v0 gate.",
            "Backtest: catalyst relaxed (require_catalyst_in_backtest=False).",
            "Attribution key on every trade: found_by_agent=large_cap_sniper.",
        ]
    )


LARGE_CAP_SNIPER = LargeCapSniperSpec()

"""Mode C — Mid-Cap Sniper (intraday). $2B–$10B band between large and speculative."""

from decimal import Decimal

from pydantic import BaseModel, Field

from trading_lab.agents.sniper.shared_execution import SNIPER_SHARED, SniperSharedExecution


class MidCapSniperSpec(BaseModel):
    agent_id: str = "mid_cap_sniper"
    family: str = "sniper"
    mode_name: str = "Mid-Cap Sniper"
    source: str = "Trading lab — fills mid-cap intraday gap ($2B–$10B)"

    min_market_cap_usd: Decimal = Decimal("2000000000")
    max_market_cap_usd: Decimal = Decimal("10000000000")  # exclusive; large starts at $10B
    profit_target_pct_min: Decimal = Decimal("6.0")
    profit_target_pct_max: Decimal = Decimal("8.0")
    stop_loss_pct_min: Decimal = Decimal("2.5")
    stop_loss_pct_max: Decimal = Decimal("3.5")
    default_profit_target_pct: Decimal = Decimal("8.0")
    default_stop_loss_pct: Decimal = Decimal("3.0")
    bar_timeframe: str = "1Min"
    min_rvol: Decimal = Decimal("2.0")
    min_rvol_paper: Decimal = Decimal("1.5")
    require_catalyst: bool = True
    require_catalyst_in_backtest: bool = False
    require_catalyst_in_paper: bool = False
    require_price_above_vwap: bool = True
    require_aligned_with_spy_qqq: bool = True
    catalyst_types: list[str] = Field(
        default_factory=lambda: [
            "earnings",
            "analyst_upgrade",
            "sector_momentum",
            "partnership",
            "other_significant_news",
        ]
    )
    shared: SniperSharedExecution = Field(default_factory=lambda: SNIPER_SHARED)
    notes: list[str] = Field(
        default_factory=lambda: [
            "Intraday mid-cap — market cap $2B–<$10B.",
            "RVOL ≥2 (≥1.5 paper); price > VWAP; SPY/QQQ aligned.",
            "Target 8% (6–8% band); stop 2.5–3.5% (default 3%). Flat by EOD.",
            "Paper/backtest: catalyst relaxed. Uses sniper shared_execution.",
            "Attribution key: found_by_agent=mid_cap_sniper.",
        ]
    )


MID_CAP_SNIPER = MidCapSniperSpec()

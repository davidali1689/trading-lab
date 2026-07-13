"""Mode B — Speculative Sniper (intraday). From Sniper Trading Strategist v2.0."""

from decimal import Decimal

from pydantic import BaseModel, Field

from trading_lab.agents.sniper.shared_execution import SNIPER_SHARED, SniperSharedExecution


class SpeculativeSniperSpec(BaseModel):
    agent_id: str = "speculative_sniper"
    family: str = "sniper"
    mode_name: str = "Speculative Sniper"
    source: str = "Sniper Trading Strategist v2.0 — Mode B"

    max_market_cap_usd: Decimal = Decimal("2000000000")
    profit_target_pct_min: Decimal = Decimal("8.0")
    profit_target_pct_aim: Decimal = Decimal("12.0")
    stop_loss_pct_min: Decimal = Decimal("3.0")
    stop_loss_pct_max: Decimal = Decimal("5.0")
    default_profit_target_pct: Decimal = Decimal("10.0")
    default_stop_loss_pct: Decimal = Decimal("4.0")
    bar_timeframe: str = "1Min"
    min_rvol: Decimal = Decimal("5.0")
    max_float_shares: Decimal = Decimal("20000000")
    max_rsi: Decimal = Decimal("80")
    require_catalyst: bool = True
    require_catalyst_in_backtest: bool = False
    catalyst_types: list[str] = Field(
        default_factory=lambda: [
            "fda_approval",
            "partnership",
            "news",
            "other_clear_catalyst",
        ]
    )
    shared: SniperSharedExecution = Field(default_factory=lambda: SNIPER_SHARED)
    notes: list[str] = Field(
        default_factory=lambda: [
            "Intraday speculative — market cap <$2B.",
            "RVOL >5 AND float <20M; RSI <80; clear catalyst.",
            "Target ≥8% (aim 12%); stop 3–5%. Hold: flat by EOD.",
            "Uses sniper shared_execution (scale-out, 15m cool-off, HVN→LVN).",
        ]
    )


SPECULATIVE_SNIPER = SpeculativeSniperSpec()

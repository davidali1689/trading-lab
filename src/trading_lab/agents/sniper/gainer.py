"""gainer_sniper — first-hour early-band continuation (S3 miss findings)."""

from decimal import Decimal

from pydantic import BaseModel, Field

from trading_lab.agents.sniper.shared_execution import SNIPER_SHARED, SniperSharedExecution


class GainerSniperSpec(BaseModel):
    agent_id: str = "gainer_sniper"
    family: str = "sniper"
    mode_name: str = "Gainer Sniper"
    source: str = "S3 miss harvest 2026-08 — bucket A never-watchlisted liquid gainers"

    min_price: Decimal = Decimal("5")
    max_price: Decimal = Decimal("50")
    min_day_gain_pct: Decimal = Decimal("2")
    max_day_gain_pct: Decimal = Decimal("15")  # skip at/above — EOD chase
    profit_target_pct: Decimal = Decimal("6.0")
    stop_loss_pct: Decimal = Decimal("2.5")
    bar_timeframe: str = "1Min"
    min_bars: int = 10
    min_rvol: Decimal = Decimal("2.5")
    min_rvol_paper: Decimal = Decimal("2.0")
    require_catalyst: bool = False
    require_price_above_vwap: bool = True
    window_start_et: str = "09:30"
    window_end_et: str = "10:30"
    shared: SniperSharedExecution = Field(default_factory=lambda: SNIPER_SHARED)
    notes: list[str] = Field(
        default_factory=lambda: [
            "First-hour live Alpaca gainers — enter while still +2% to +15%, not EOD chase.",
            "Fixes miss-harvest bucket A (frozen most-actives never saw FGI/MB/BOXL).",
            "No Finnhub catalyst — list membership + RVOL is the signal.",
            "≥10 1Min bars (~09:40); RVOL ≥2 paper / 2.5 live; close ≥ VWAP.",
            "Price $5–$50; reject 5-letter W/U tickers and leveraged products.",
            "Target 6% / stop 2.5%; full equity/5 slice then 0.25% risk cap; flat EOD.",
            "Window 09:30–10:30 ET then release. Never force a trade.",
            "Attribution: found_by_agent=gainer_sniper.",
        ]
    )


GAINER_SNIPER = GainerSniperSpec()

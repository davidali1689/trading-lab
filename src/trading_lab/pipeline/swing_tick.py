"""Swing tick helper — evaluate swing_momentum with Unusual Whales congress soft overlay."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from trading_lab.agents.swing.momentum import SWING_MOMENTUM
from trading_lab.catalysts.congress import (
    MockUnusualWhalesCongress,
    UnusualWhalesCongress,
    congress_since,
)
from trading_lab.eval.swing import evaluate_swing_momentum
from trading_lab.market_data.mock import MockMarketData
from trading_lab.market_data.types import BarRequest, SessionContext


def _congress_port(*, use_mock: bool):
    if use_mock:
        return MockUnusualWhalesCongress()
    return UnusualWhalesCongress()


def evaluate_swing_with_congress(
    symbol: str,
    *,
    use_mock: bool = True,
    market_cap_usd: Decimal = Decimal("50000000000"),
) -> dict:
    """Score swing_momentum for a symbol; congress never forces ENTER."""
    end = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    start = end - timedelta(days=40)
    md = MockMarketData()
    bars = md.get_bars(
        BarRequest(symbol=symbol, timeframe="1Day", start=start, end=end, feed="iex")
    )
    if not bars:
        return {
            "symbol": symbol,
            "agent": "swing_momentum",
            "status": "NO_TRADE",
            "detail": "no_bars",
        }

    port = _congress_port(use_mock=use_mock)
    since = congress_since(SWING_MOMENTUM.congress_lookback_days)
    signals = (
        port.signals_for(symbol, since=since) if SWING_MOMENTUM.congress_catalyst_enabled else []
    )

    ctx = SessionContext(
        symbol=symbol,
        bar=bars[-1],
        bars=bars,
        market_cap_usd=market_cap_usd,
        price_above_8ema=True,
        spy_or_qqq_above_20dma=True,
        rs_vs_spy_qqq=True,
        catalyst_signals=signals,
        has_catalyst=any(s.direction == "buy" for s in signals),
    )
    decision = evaluate_swing_momentum(ctx)
    return {
        "symbol": symbol,
        "agent": decision.agent_id,
        "status": decision.status.value,
        "catalyst": decision.catalyst,
        "rvol": str(decision.rvol) if decision.rvol is not None else None,
        "priority": decision.meta.get("priority", 0),
        "congress_action": decision.meta.get("congress_action"),
        "reason": decision.reason,
        "source": SWING_MOMENTUM.congress_source,
        "congress_enabled": SWING_MOMENTUM.congress_catalyst_enabled,
    }

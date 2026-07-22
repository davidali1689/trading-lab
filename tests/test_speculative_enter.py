"""Speculative sniper must ENTER on paper when micro-cap gates pass."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from trading_lab.agents.sniper.shared_execution import SniperStatus
from trading_lab.eval.speculative import evaluate_speculative_sniper
from trading_lab.market_data.types import Bar, SessionContext
from trading_lab.schemas.trades import RunMode


def _bars(*, volume_last: Decimal = Decimal("1000")) -> list[Bar]:
    out: list[Bar] = []
    for i in range(21):
        vol = Decimal("100") if i < 20 else volume_last
        out.append(
            Bar(
                symbol="ELVA",
                ts=datetime(2026, 7, 15, 15, i, tzinfo=timezone.utc),
                open=Decimal("5"),
                high=Decimal("5.2"),
                low=Decimal("4.9"),
                close=Decimal("5"),
                volume=vol,
                vwap=Decimal("4.95"),
            )
        )
    return out


def test_paper_speculative_enters_with_catalyst_float_rsi():
    bars = _bars()
    ctx = SessionContext(
        symbol="ELVA",
        bar=bars[-1],
        bars=bars,
        market_cap_usd=Decimal("500000000"),
        has_catalyst=True,
        rvol=Decimal("5.0"),
        float_shares=None,
        rsi=None,
    )
    d = evaluate_speculative_sniper(ctx, mode=RunMode.PAPER)
    assert d.status == SniperStatus.ENTER
    assert d.agent_id == "speculative_sniper"
    assert d.trade_map is not None


def test_live_speculative_requires_catalyst():
    bars = _bars()
    ctx = SessionContext(
        symbol="ELVA",
        bar=bars[-1],
        bars=bars,
        market_cap_usd=Decimal("500000000"),
        has_catalyst=False,
        rvol=Decimal("6.0"),
    )
    d = evaluate_speculative_sniper(ctx, mode=RunMode.LIVE)
    assert d.status != SniperStatus.ENTER
    assert "catalyst" in (d.reason or "")


def test_speculative_rejects_large_cap():
    bars = _bars()
    ctx = SessionContext(
        symbol="AAPL",
        bar=bars[-1],
        bars=bars,
        market_cap_usd=Decimal("3000000000000"),
        rvol=Decimal("6.0"),
        has_catalyst=True,
    )
    d = evaluate_speculative_sniper(ctx, mode=RunMode.PAPER)
    assert d.status != SniperStatus.ENTER
    assert "market_cap" in (d.reason or "")

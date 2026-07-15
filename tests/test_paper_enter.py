"""Paper eval: setups must be reachable and ENTER must submit."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from trading_lab.agents.sniper.shared_execution import SniperStatus
from trading_lab.eval.large_cap import evaluate_large_cap_sniper
from trading_lab.market_data.types import Bar, SessionContext
from trading_lab.schemas.trades import RunMode


def _bar(*, close: Decimal, vwap: Decimal, volume: Decimal) -> Bar:
    return Bar(
        symbol="AAPL",
        ts=datetime(2026, 7, 15, 15, 0, tzinfo=timezone.utc),
        open=close,
        high=close,
        low=close,
        close=close,
        volume=volume,
        vwap=vwap,
    )


def _ctx(*, rvol: Decimal, above_vwap: bool, has_catalyst: bool = False) -> SessionContext:
    close = Decimal("100")
    vwap = Decimal("99") if above_vwap else Decimal("101")
    bars = [_bar(close=close, vwap=vwap, volume=Decimal("100")) for _ in range(21)]
    # Spike last bar volume so computed rvol is high if ctx.rvol not set
    bars[-1] = _bar(close=close, vwap=vwap, volume=Decimal("1000"))
    return SessionContext(
        symbol="AAPL",
        bar=bars[-1],
        bars=bars,
        market_cap_usd=Decimal("3000000000000"),
        has_catalyst=has_catalyst,
        rvol=rvol,
        above_vwap=above_vwap,
        spy_aligned=True,
        qqq_aligned=True,
    )


def test_paper_enters_without_catalyst_when_rvol_and_vwap_ok():
    """Paper must be evaluable — catalyst is relaxed like backtest."""
    d = evaluate_large_cap_sniper(_ctx(rvol=Decimal("2.0"), above_vwap=True), mode=RunMode.PAPER)
    assert d.status == SniperStatus.ENTER
    assert d.trade_map is not None


def test_live_still_requires_catalyst():
    d = evaluate_large_cap_sniper(_ctx(rvol=Decimal("2.0"), above_vwap=True), mode=RunMode.LIVE)
    assert d.status != SniperStatus.ENTER
    assert "catalyst" in (d.reason or "")


def test_paper_enters_at_paper_min_rvol():
    """Paper uses a slightly softer RVOL floor so setups appear in the journal."""
    d = evaluate_large_cap_sniper(_ctx(rvol=Decimal("1.25"), above_vwap=True), mode=RunMode.PAPER)
    assert d.status == SniperStatus.ENTER


def test_paper_still_blocks_weak_rvol():
    d = evaluate_large_cap_sniper(_ctx(rvol=Decimal("0.8"), above_vwap=True), mode=RunMode.PAPER)
    assert d.status != SniperStatus.ENTER

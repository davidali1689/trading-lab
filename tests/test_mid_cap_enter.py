"""Mid-cap sniper must ENTER on paper when $2B–$10B gates pass."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from trading_lab.agents.sniper.shared_execution import SniperStatus
from trading_lab.eval.mid_cap import evaluate_mid_cap_sniper
from trading_lab.market_data.types import Bar, SessionContext
from trading_lab.schemas.trades import RunMode


def _bars(*, volume_last: Decimal = Decimal("1000")) -> list[Bar]:
    out: list[Bar] = []
    for i in range(21):
        vol = Decimal("100") if i < 20 else volume_last
        out.append(
            Bar(
                symbol="XYZ",
                ts=datetime(2026, 7, 15, 15, i, tzinfo=timezone.utc),
                open=Decimal("40"),
                high=Decimal("40.5"),
                low=Decimal("39.8"),
                close=Decimal("40"),
                volume=vol,
                vwap=Decimal("39.5"),
            )
        )
    return out


def test_paper_mid_cap_enters_with_8pct_target():
    bars = _bars()
    ctx = SessionContext(
        symbol="XYZ",
        bar=bars[-1],
        bars=bars,
        market_cap_usd=Decimal("5000000000"),
        has_catalyst=False,
        rvol=Decimal("2.0"),
        above_vwap=True,
        spy_aligned=True,
        qqq_aligned=True,
    )
    d = evaluate_mid_cap_sniper(ctx, mode=RunMode.PAPER)
    assert d.status == SniperStatus.ENTER
    assert d.agent_id == "mid_cap_sniper"
    assert d.trade_map is not None
    # 8% target on $40 entry → $43.20
    assert d.trade_map.final_take_profit == Decimal("43.20")
    # 3% stop → $38.80
    assert d.trade_map.stop_loss == Decimal("38.80")


def test_live_mid_cap_requires_catalyst():
    bars = _bars()
    ctx = SessionContext(
        symbol="XYZ",
        bar=bars[-1],
        bars=bars,
        market_cap_usd=Decimal("5000000000"),
        has_catalyst=False,
        rvol=Decimal("2.5"),
        above_vwap=True,
        spy_aligned=True,
    )
    d = evaluate_mid_cap_sniper(ctx, mode=RunMode.LIVE)
    assert d.status != SniperStatus.ENTER
    assert "catalyst" in (d.reason or "")


def test_mid_cap_rejects_outside_band():
    bars = _bars()
    for cap in (Decimal("500000000"), Decimal("3000000000000")):
        ctx = SessionContext(
            symbol="XYZ",
            bar=bars[-1],
            bars=bars,
            market_cap_usd=cap,
            rvol=Decimal("2.5"),
            above_vwap=True,
            spy_aligned=True,
            has_catalyst=True,
        )
        d = evaluate_mid_cap_sniper(ctx, mode=RunMode.PAPER)
        assert d.status != SniperStatus.ENTER
        assert "market_cap" in (d.reason or "")

"""Swing momentum + Unusual Whales congress soft-catalyst tests."""

from datetime import UTC, datetime
from decimal import Decimal

from trading_lab.agents.swing.decision import SwingStatus
from trading_lab.agents.swing.momentum import SWING_MOMENTUM
from trading_lab.catalysts.congress import MockUnusualWhalesCongress
from trading_lab.catalysts.types import CatalystKind, CatalystSignal
from trading_lab.eval.swing import apply_congress_soft_overlay, evaluate_swing_momentum
from trading_lab.market_data.types import Bar, SessionContext


def _bar(symbol: str = "MSFT", close: str = "400") -> Bar:
    return Bar(
        symbol=symbol,
        ts=datetime(2026, 7, 10, 20, 0, tzinfo=UTC),
        open=Decimal(close),
        high=Decimal(close),
        low=Decimal(close),
        close=Decimal(close),
        volume=Decimal("2000000"),
        timeframe="1Day",
    )


def _ctx(**kwargs) -> SessionContext:
    base = dict(
        symbol="MSFT",
        bar=_bar(),
        bars=[_bar() for _ in range(25)],
        rvol=Decimal("2.0"),
        market_cap_usd=Decimal("2000000000000"),
        price_above_8ema=True,
        spy_or_qqq_above_20dma=True,
        rs_vs_spy_qqq=True,
    )
    base.update(kwargs)
    return SessionContext(**base)


def _congress(direction: str, symbol: str = "MSFT") -> CatalystSignal:
    return CatalystSignal(
        kind=CatalystKind.CONGRESS_TRADE,
        symbol=symbol,
        direction=direction,  # type: ignore[arg-type]
        disclosed_at=datetime(2026, 7, 1, tzinfo=UTC),
        source="unusual_whales",
        politician="Test Member",
        amounts="$1,000 - $15,000",
    )


def test_swing_enter_without_congress():
    d = evaluate_swing_momentum(_ctx())
    assert d.status == SwingStatus.ENTER
    assert d.trade_map is not None
    assert d.meta.get("priority", 0) == 0


def test_congress_buy_raises_priority_not_required():
    d = evaluate_swing_momentum(_ctx(catalyst_signals=[_congress("buy")]))
    assert d.status == SwingStatus.ENTER
    assert d.meta["priority"] >= 1
    assert d.meta["congress_action"] == "priority_boost_buy"
    assert "congress_buy" in d.catalyst


def test_congress_sell_soft_skips_enter():
    d = evaluate_swing_momentum(_ctx(catalyst_signals=[_congress("sell")]))
    assert d.status == SwingStatus.WATCH
    assert d.trade_map is None
    assert d.meta["congress_action"] == "soft_skip_sell"


def test_congress_buy_never_forces_enter():
    d = evaluate_swing_momentum(
        _ctx(
            rvol=Decimal("0.5"),
            catalyst_signals=[_congress("buy")],
        )
    )
    assert d.status == SwingStatus.NO_TRADE
    assert d.meta.get("congress_action") == "ignored_no_setup"


def test_soft_overlay_invariant():
    status, note, meta = apply_congress_soft_overlay(
        SwingStatus.NO_TRADE,
        signals=[_congress("buy")],
        enabled=True,
        soft_only=False,
        meta={"priority": 0},
    )
    assert status == SwingStatus.NO_TRADE
    assert "force" in note or meta.get("congress_action")


def test_mock_unusual_whales_filter():
    mock = MockUnusualWhalesCongress([_congress("buy", "MSFT"), _congress("sell", "AAPL")])
    since = datetime(2026, 6, 1, tzinfo=UTC)
    msft = mock.signals_for("MSFT", since=since)
    assert len(msft) == 1
    assert msft[0].direction == "buy"
    assert mock.signals_for("AAPL", since=since)[0].direction == "sell"


def test_swing_spec_has_congress_knobs():
    assert SWING_MOMENTUM.congress_catalyst_enabled is True
    assert SWING_MOMENTUM.congress_soft_only is True
    assert SWING_MOMENTUM.congress_source == "unusual_whales"
    assert any("Unusual Whales" in n for n in SWING_MOMENTUM.notes)

from datetime import date, datetime, timezone
from decimal import Decimal
from uuid import uuid4

from trading_lab.agents import AGENTS, all_agent_notes, get_agent
from trading_lab.agents.sniper import (
    SNIPER_SHARED,
    SniperDecision,
    SniperStatus,
    TradeMap,
    in_cooling_off,
    scale_out_price,
)
from trading_lab.agents.swing import (
    SWING_MOMENTUM,
    CapTier,
    SwingDecision,
    SwingStatus,
    SwingTradeMap,
)
from trading_lab.config.vendors import V1_VENDORS, VendorId
from trading_lab.schemas import (
    AgentAccuracyReport,
    ExitReason,
    RunMode,
    Side,
    TradeRecord,
)
from trading_lab.schemas.hold import HoldPlan, StrategyHorizon


def test_vendors_locked():
    assert V1_VENDORS.primary_bars == VendorId.ALPACA
    assert V1_VENDORS.secondary_quotes == VendorId.FINNHUB


def test_agents_registered():
    assert set(AGENTS) == {
        "large_cap_sniper",
        "speculative_sniper",
        "swing_momentum",
    }
    assert get_agent("swing_momentum").family == "swing"


def test_notes_cover_families():
    notes = all_agent_notes()
    assert "sniper_shared_execution" in notes
    assert "swing_shared_execution" in notes
    assert any("PDT" in n for n in notes["swing_shared_execution"])


def test_trade_pnl_and_hold():
    hold = HoldPlan(
        horizon=StrategyHorizon.INTRADAY,
        min_hold_sessions=0,
        typical_hold_sessions=0,
        max_hold_sessions=0,
        summary="Flat by EOD",
    )
    t = TradeRecord(
        trade_id=uuid4(),
        run_id=uuid4(),
        found_by_agent="large_cap_sniper",
        symbol="SPY",
        side=Side.LONG,
        mode=RunMode.SIM,
        entry_ts=datetime(2026, 1, 2, 14, 35, tzinfo=timezone.utc),
        entry_px=Decimal("100"),
        qty=Decimal("10"),
        exit_ts=datetime(2026, 1, 2, 15, 0, tzinfo=timezone.utc),
        exit_px=Decimal("101"),
        exit_reason=ExitReason.TARGET,
        fees=Decimal("1"),
        bars_held=25,
        hold_plan=hold,
    )
    assert t.pnl_usd == Decimal("9")
    assert t.pnl_pct == Decimal("0.9")
    assert t.found_by_agent == "large_cap_sniper"
    assert t.agent_id == "large_cap_sniper"


def test_swing_hold_requires_overnight():
    try:
        HoldPlan(
            horizon=StrategyHorizon.SWING,
            min_hold_sessions=0,
            typical_hold_sessions=1,
            max_hold_sessions=5,
            summary="bad",
        )
        raised = False
    except ValueError:
        raised = True
    assert raised


def test_sniper_shared_execution():
    assert scale_out_price(Decimal("100"), Decimal("104")) == Decimal("102")
    stop_ts = datetime(2026, 1, 2, 15, 0, tzinfo=timezone.utc)
    assert in_cooling_off(stop_ts, datetime(2026, 1, 2, 15, 10, tzinfo=timezone.utc))
    assert SNIPER_SHARED.cooling_off_after_stop.total_seconds() == 15 * 60


def test_sniper_decision_includes_hold():
    enter = SniperDecision(
        agent_id="large_cap_sniper",
        symbol="AAPL",
        status=SniperStatus.ENTER,
        catalyst="Earnings beat",
        trade_map=TradeMap(
            entry_trigger=Decimal("190"),
            scale_out_point=Decimal("193.325"),
            final_take_profit=Decimal("196.65"),
            stop_loss=Decimal("186.675"),
        ),
    )
    intent = enter.to_trade_intent(Decimal("10"))
    assert intent is not None
    assert intent.hold_plan.horizon == StrategyHorizon.INTRADAY
    assert "Flat by" in intent.hold_plan.summary or "flat" in intent.hold_plan.summary.lower()


def test_swing_decision_hold():
    plan = SWING_MOMENTUM.hold_plan_for_tier(CapTier.MICRO)
    assert plan.min_hold_sessions >= 1
    d = SwingDecision(
        symbol="ABCD",
        status=SwingStatus.ENTER,
        cap_tier=CapTier.MICRO,
        catalyst="FDA",
        trade_map=SwingTradeMap(
            entry_trigger=Decimal("2.00"),
            scale_out_point=Decimal("2.08"),
            final_take_profit=Decimal("2.24"),
            stop_loss=Decimal("1.90"),
        ),
        hold_plan=plan,
    )
    intent = d.to_trade_intent(Decimal("100"))
    assert intent is not None
    assert intent.hold_plan.typical_hold_sessions == 2
    assert "overnight" in intent.hold_plan.summary.lower()


def test_agent_expectancy():
    r = AgentAccuracyReport(
        agent_id="swing_momentum",
        window_start=date(2026, 1, 1),
        window_end=date(2026, 2, 28),
        symbols=["SPY"],
        wins=6,
        losses=4,
        trades_taken=10,
        setup_fires=20,
        avg_win_usd=Decimal("50"),
        avg_loss_usd=Decimal("-30"),
    )
    assert r.expectancy_usd == Decimal("18")


def test_swing_rvol_gates():
    assert SWING_MOMENTUM.rvol_gate(Decimal("15000000000")) == Decimal("1.25")
    assert SWING_MOMENTUM.rvol_gate(Decimal("5000000000")) == Decimal("1.50")
    assert SWING_MOMENTUM.rvol_gate(Decimal("500000000")) == Decimal("2.00")

from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from trading_lab.agents.sniper import SNIPER_SHARED
from trading_lab.execution import DEFAULT_FILL_MODEL, RiskGate
from trading_lab.improvement import IMPROVEMENT
from trading_lab.journal import SqliteJournal, export_journal_csv
from trading_lab.market_data.types import Bar
from trading_lab.pipeline import run_vertical_slice, smoke_eval_on_mock_bar, walk_forward_bakeoff
from trading_lab.schemas.trades import Side


def test_hvn_deferred():
    assert SNIPER_SHARED.require_hvn_break_into_lvn is False
    assert SNIPER_SHARED.hvn_lvn_deferred is True


def test_fill_model_adverse_slippage():
    signal = Bar(
        symbol="AAPL",
        ts=datetime(2026, 1, 2, 15, 0, tzinfo=timezone.utc),
        open=Decimal("100"),
        high=Decimal("101"),
        low=Decimal("99"),
        close=Decimal("100.5"),
        volume=Decimal("1"),
    )
    nxt = Bar(
        symbol="AAPL",
        ts=datetime(2026, 1, 2, 15, 1, tzinfo=timezone.utc),
        open=Decimal("100"),
        high=Decimal("101"),
        low=Decimal("99"),
        close=Decimal("100.2"),
        volume=Decimal("1"),
    )
    px = DEFAULT_FILL_MODEL.fill_price(signal, nxt, Side.LONG)
    assert px > Decimal("100")


def test_risk_gate_blocks_max_positions():
    gate = RiskGate()
    gate.state.open_positions = 3
    from trading_lab.schemas.hold import HoldPlan, StrategyHorizon
    from trading_lab.schemas.trades import TradeIntent

    intent = TradeIntent(
        found_by_agent="large_cap_sniper",
        symbol="AAPL",
        side=Side.LONG,
        entry_px=Decimal("100"),
        qty=Decimal("1"),
        hold_plan=HoldPlan(
            horizon=StrategyHorizon.INTRADAY,
            min_hold_sessions=0,
            typical_hold_sessions=0,
            max_hold_sessions=0,
            summary="flat",
        ),
    )
    d = gate.check(intent, datetime(2026, 1, 2, 15, 0, tzinfo=timezone.utc))
    assert d.allowed is False


def test_vertical_slice_and_grafana_export(tmp_path: Path):
    db = tmp_path / "journal.sqlite"
    out = tmp_path / "grafana"
    summary = run_vertical_slice(journal_path=str(db))
    assert summary["trades"] + summary["skips"] > 0
    assert "large_cap_sniper" in summary["found_by_agents"] or summary["trades"] == 0
    journal = SqliteJournal(db)
    assert journal.count_trades() == summary["trades"]
    paths = export_journal_csv(db, out)
    assert paths["trades"].exists()
    assert paths["skips"].exists()


def test_walk_forward_lists_all_agents(tmp_path: Path):
    report = walk_forward_bakeoff(journal_path=str(tmp_path / "wf.sqlite"))
    ids = {a.agent_id for a in report.agents}
    assert ids == {
        "large_cap_sniper",
        "mid_cap_sniper",
        "speculative_sniper",
        "swing_momentum",
    }


def test_smoke_eval():
    assert smoke_eval_on_mock_bar() in {"ENTER", "WATCH", "NO_TRADE"}


def test_improvement_stack_mentions_langfuse():
    blob = " ".join(IMPROVEMENT.llm_coach_optional + IMPROVEMENT.notes)
    assert "Langfuse" in blob
    assert "found_by_agent" in " ".join(IMPROVEMENT.notes)

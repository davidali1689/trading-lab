"""Weekly scorecard: improving / worse / propose_revert flag."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

from trading_lab.agents.sniper.shared_execution import SNIPER_SHARED
from trading_lab.improvement.coach_client import CoachClient
from trading_lab.improvement.friday_review import run_friday_review
from trading_lab.improvement.scorecard import (
    build_weekly_scorecard,
    prior_week_id,
    week_id_for,
)
from trading_lab.journal.sqlite import SqliteJournal
from trading_lab.schemas.scorecard import AgentScorecard, Trend, WeeklyScorecard
from trading_lab.schemas.trades import ExitReason, RunMode, Side, TradeRecord


def _trade(
    j: SqliteJournal, *, agent: str, symbol: str, entry: Decimal, exit_px: Decimal, ts: datetime
) -> None:
    j.write_trade(
        TradeRecord(
            trade_id=uuid4(),
            run_id=uuid4(),
            found_by_agent=agent,
            symbol=symbol,
            side=Side.LONG,
            mode=RunMode.PAPER,
            setup_tags=["t"],
            entry_ts=ts,
            entry_px=entry,
            qty=Decimal("1"),
            stop_px=entry * Decimal("0.98"),
            target_px=entry * Decimal("1.03"),
            hold_plan=SNIPER_SHARED.default_hold_plan,
            exit_ts=ts + timedelta(minutes=5),
            exit_px=exit_px,
            exit_reason=ExitReason.TARGET if exit_px > entry else ExitReason.STOP,
            bars_held=2,
        )
    )


def test_scorecard_worse_sets_propose_revert(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("MOCK_BEDROCK", "true")
    db = tmp_path / "j.sqlite"
    j = SqliteJournal(db)
    # Pin "now" week via trades in current ISO week
    week = week_id_for()
    # Monday of current week
    y, w, _ = datetime.now(UTC).isocalendar()
    monday = datetime.fromisocalendar(y, w, 1).replace(tzinfo=UTC)
    _trade(
        j,
        agent="large_cap_sniper",
        symbol="AAA",
        entry=Decimal("100"),
        exit_px=Decimal("90"),
        ts=monday + timedelta(hours=10),
    )
    prior = WeeklyScorecard(
        week_id=prior_week_id(week),
        built_at=monday.isoformat(),
        agents={
            "large_cap_sniper": AgentScorecard(
                agent_id="large_cap_sniper",
                trade_count=2,
                expectancy_usd="20",
                composite="40",
                trend=Trend.FLAT,
            ),
            "mid_cap_sniper": AgentScorecard(agent_id="mid_cap_sniper", composite="0"),
            "speculative_sniper": AgentScorecard(agent_id="speculative_sniper", composite="0"),
            "gainer_sniper": AgentScorecard(agent_id="gainer_sniper", composite="0"),
            "swing_momentum": AgentScorecard(agent_id="swing_momentum", composite="0"),
        },
    )
    card = build_weekly_scorecard(db, week_id=week, miss_shards=[], prior=prior)
    lc = card.agents["large_cap_sniper"]
    assert lc.trade_count == 1
    assert lc.trend == Trend.WORSE
    assert lc.propose_revert is True


def test_friday_review_pack(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("MOCK_BEDROCK", "true")
    monkeypatch.delenv("JOURNAL_S3_BUCKET", raising=False)
    db = tmp_path / "j.sqlite"
    SqliteJournal(db)
    # Avoid live Alpaca in miss harvest
    from trading_lab.improvement import friday_review as fr
    from trading_lab.schemas.misses import DailyMissReport

    empty = DailyMissReport(
        day="2026-07-17",
        built_at=datetime.now(UTC).isoformat(),
        detail="test",
    )
    pack = run_friday_review(
        db,
        report=empty,
        miss_shards=[],
        prior_scorecard=WeeklyScorecard(
            week_id="2026-W01",
            built_at=datetime.now(UTC).isoformat(),
            agents={
                aid: AgentScorecard(agent_id=aid, composite="0")
                for aid in (
                    "large_cap_sniper",
                    "mid_cap_sniper",
                    "speculative_sniper",
                    "gainer_sniper",
                    "swing_momentum",
                )
            },
        ),
    )
    assert pack["ok"] is True
    assert "scorecard" in pack
    assert len(pack["coaches"]["coaches"]) == 5
    # silence unused import lint in some runners
    _ = fr
    _ = CoachClient

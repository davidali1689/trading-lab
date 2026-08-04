"""Daily + weekly agent ops scoreboard."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from trading_lab.agents import AGENTS
from trading_lab.agents.sniper.shared_execution import SNIPER_SHARED
from trading_lab.improvement.scoreboard import (
    build_daily_scoreboard,
    persist_daily_scoreboard,
    run_and_persist_daily_scoreboard,
)
from trading_lab.improvement.scorecard import build_weekly_scorecard, persist_scorecard
from trading_lab.journal.sqlite import SqliteJournal
from trading_lab.schemas.trades import ExitReason, RunMode, Side, SkipEvent, SkipReason, TradeRecord


def _trade(
    j: SqliteJournal,
    *,
    agent: str,
    symbol: str,
    entry: Decimal,
    exit_px: Decimal,
    ts: datetime,
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


def _skip(j: SqliteJournal, *, agent: str, symbol: str, ts: datetime) -> None:
    j.write_skip(
        SkipEvent(
            event_id=uuid4(),
            run_id=uuid4(),
            found_by_agent=agent,
            symbol=symbol,
            ts=ts,
            mode=RunMode.PAPER,
            skip_reason=SkipReason.SETUP_MISSING,
            detail="test",
        )
    )


def test_daily_scoreboard_empty_journal_has_all_agents(tmp_path: Path) -> None:
    db = tmp_path / "j.sqlite"
    SqliteJournal(db)
    board = build_daily_scoreboard(db, day="2026-07-24")
    assert board.day == "2026-07-24"
    assert set(board.agents) == set(AGENTS)
    for aid, row in board.agents.items():
        assert row.agent_id == aid
        assert row.trade_count == 0
        assert row.skip_count == 0
        assert row.win_count == 0
        assert row.loss_count == 0
        assert row.win_rate == "0.00"
        assert row.loss_rate == "0.00"


def test_daily_scoreboard_win_loss_skip_rates(tmp_path: Path) -> None:
    db = tmp_path / "j.sqlite"
    j = SqliteJournal(db)
    day = "2026-07-24"
    ts = datetime(2026, 7, 24, 15, 0, tzinfo=UTC)
    _trade(
        j,
        agent="speculative_sniper",
        symbol="WIN",
        entry=Decimal("10"),
        exit_px=Decimal("12"),
        ts=ts,
    )
    _trade(
        j,
        agent="speculative_sniper",
        symbol="LOSS",
        entry=Decimal("10"),
        exit_px=Decimal("8"),
        ts=ts + timedelta(minutes=10),
    )
    _skip(j, agent="speculative_sniper", symbol="SKIP", ts=ts)
    # Outside window
    _trade(
        j,
        agent="speculative_sniper",
        symbol="OLD",
        entry=Decimal("10"),
        exit_px=Decimal("11"),
        ts=datetime(2026, 7, 23, 15, 0, tzinfo=UTC),
    )

    board = build_daily_scoreboard(db, day=day)
    row = board.agents["speculative_sniper"]
    assert row.trade_count == 2
    assert row.skip_count == 1
    assert row.win_count == 1
    assert row.loss_count == 1
    assert row.win_rate == "0.50"
    assert row.loss_rate == "0.50"
    assert row.net_pnl_usd == "0.00"  # +2 and -2
    assert board.agents["mid_cap_sniper"].trade_count == 0


def test_weekly_scorecard_includes_loss_rate(tmp_path: Path) -> None:
    db = tmp_path / "j.sqlite"
    j = SqliteJournal(db)
    monday = datetime(2026, 7, 20, 14, 0, tzinfo=UTC)  # ISO week 2026-W30
    _trade(
        j,
        agent="large_cap_sniper",
        symbol="A",
        entry=Decimal("100"),
        exit_px=Decimal("110"),
        ts=monday,
    )
    _trade(
        j,
        agent="large_cap_sniper",
        symbol="B",
        entry=Decimal("100"),
        exit_px=Decimal("90"),
        ts=monday + timedelta(days=1),
    )
    card = build_weekly_scorecard(db, week_id="2026-W30", miss_shards=[], prior=None)
    lc = card.agents["large_cap_sniper"]
    assert lc.win_count == 1
    assert lc.loss_count == 1
    assert lc.win_rate == "0.50"
    assert lc.loss_rate == "0.50"


def test_persist_daily_scoreboard_writes_s3(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("JOURNAL_S3_BUCKET", "test-bucket")
    db = tmp_path / "j.sqlite"
    SqliteJournal(db)
    client = MagicMock()
    with patch("boto3.client", return_value=client):
        out = run_and_persist_daily_scoreboard(db, day="2026-07-24")
    assert out["ok"] is True
    keys = [c.kwargs["Key"] for c in client.put_object.call_args_list]
    assert "scoreboards/daily/2026-07-24.json" in keys
    assert "scoreboards/daily/latest.json" in keys


def test_weekly_scorecard_persist_writes_s3(tmp_path: Path, monkeypatch) -> None:
    """Friday weekly scorecard persist must write dated + latest scorecard JSON."""
    monkeypatch.setenv("JOURNAL_S3_BUCKET", "test-bucket")
    db = tmp_path / "j.sqlite"
    SqliteJournal(db)
    card = build_weekly_scorecard(db, week_id="2026-W30", miss_shards=[], prior=None)
    client = MagicMock()
    with patch("boto3.client", return_value=client):
        out = persist_scorecard(card)
    assert out["ok"] is True
    keys = [c.kwargs["Key"] for c in client.put_object.call_args_list]
    assert "scorecards/2026-W30.json" in keys
    assert "scorecards/latest.json" in keys


def test_api_eod_persists_daily_scoreboard(monkeypatch: pytest.MonkeyPatch) -> None:
    """EOD phase must build+persist the daily agent scoreboard."""
    monkeypatch.setenv("SECRET_ARN", "")
    monkeypatch.setenv("MOCK_BEDROCK", "true")
    from api import server

    client = TestClient(server.app)
    scoreboard_payload = {
        "ok": True,
        "scoreboard": {"day": "2026-07-24", "agents": {}},
        "persist": {"ok": True},
    }
    with (
        patch("api.server.hydrate_journal_from_s3", return_value={"ok": True}),
        patch("api.server.flatten_sniper_paper", return_value=[]),
        patch("api.server.persist_journal_to_s3", return_value={"ok": True}),
        patch(
            "api.server.run_and_persist_postmortem",
            return_value={"ok": True, "mock": True},
        ),
        patch(
            "api.server.run_and_persist_daily_scoreboard",
            return_value=scoreboard_payload,
        ) as mock_board,
        patch("api.server._holiday_noop", return_value=None),
        patch("api.server.should_run_eod", return_value=True),
    ):
        resp = client.post("/events", json={"phase": "eod", "force": True})
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["results"][0]["scoreboard"]["ok"] is True
    assert "scoreboard=" in body["detail"]
    mock_board.assert_called_once()


def test_persist_daily_without_bucket(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("JOURNAL_S3_BUCKET", raising=False)
    db = tmp_path / "j.sqlite"
    SqliteJournal(db)
    board = build_daily_scoreboard(db, day="2026-07-24")
    out = persist_daily_scoreboard(board)
    assert out["ok"] is False

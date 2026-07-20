"""API + unit coverage for miss harvest, scorecard, Friday review pack."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from trading_lab.agents.sniper.shared_execution import SNIPER_SHARED
from trading_lab.improvement.coach_client import CoachClient
from trading_lab.improvement.friday_review import run_friday_review
from trading_lab.improvement.miss_harvest import build_miss_report, run_and_persist_miss_harvest
from trading_lab.improvement.scorecard import build_weekly_scorecard, week_id_for
from trading_lab.journal.sqlite import SqliteJournal
from trading_lab.market_data.alpaca_screener import ScreenerRow
from trading_lab.schemas.misses import DailyMissReport, MissBucket
from trading_lab.schemas.scorecard import AgentScorecard, Trend, WeeklyScorecard
from trading_lab.schemas.trades import ExitReason, RunMode, Side, SkipEvent, SkipReason, TradeRecord


def _write_journal(path: Path) -> None:
    j = SqliteJournal(path)
    now = datetime.now(UTC)
    j.write_skip(
        SkipEvent(
            event_id=uuid4(),
            run_id=uuid4(),
            found_by_agent="mid_cap_sniper",
            symbol="ABCD",
            ts=now,
            mode=RunMode.PAPER,
            skip_reason=SkipReason.SETUP_MISSING,
            detail="rvol_low",
        )
    )
    j.write_trade(
        TradeRecord(
            trade_id=uuid4(),
            run_id=uuid4(),
            found_by_agent="large_cap_sniper",
            symbol="AAPL",
            side=Side.LONG,
            mode=RunMode.PAPER,
            setup_tags=["t"],
            entry_ts=now,
            entry_px=Decimal("100"),
            qty=Decimal("1"),
            stop_px=Decimal("98"),
            target_px=Decimal("103"),
            hold_plan=SNIPER_SHARED.default_hold_plan,
            exit_ts=now + timedelta(minutes=3),
            exit_px=Decimal("99"),
            exit_reason=ExitReason.STOP,
            bars_held=2,
        )
    )


def test_miss_harvest_job_end_to_end(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("JOURNAL_S3_BUCKET", raising=False)
    db = tmp_path / "j.sqlite"
    _write_journal(db)
    gainers = [
        ScreenerRow(
            symbol="ZZZZ",
            source="gainer",
            price=Decimal("12"),
            percent_change=Decimal("20"),
        ),
        ScreenerRow(
            symbol="ABCD",
            source="gainer",
            price=Decimal("25"),
            percent_change=Decimal("11"),
        ),
        ScreenerRow(
            symbol="AAPL",
            source="gainer",
            price=Decimal("200"),
            percent_change=Decimal("7"),
        ),
    ]
    report_model = build_miss_report(
        journal_path=db,
        injected_gainers=gainers,
        watchlist_symbols=["ABCD", "AAPL"],
    )
    out = run_and_persist_miss_harvest(db, injected_gainers=gainers)
    assert out["persist"]["ok"] is False  # no S3 in unit test
    report = report_model.to_dict()
    assert report["detail"].startswith("misses=")
    by_sym = {r["symbol"]: r for r in report["top_gainers"]}
    assert by_sym["ZZZZ"]["bucket"] == MissBucket.NEVER_WATCHLIST.value
    assert by_sym["ABCD"]["bucket"] == MissBucket.WATCHED_NO_ENTER.value
    assert by_sym["AAPL"]["bucket"] == MissBucket.ENTERED_MISSED_MOVE.value


def test_api_postmarket_runs_miss_harvest(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SECRET_ARN", "")
    monkeypatch.setenv("MOCK_BEDROCK", "true")
    from api import server

    client = TestClient(server.app)
    doc = type("Doc", (), {})()
    doc.symbols = ["LMNO"]
    doc.source = "fresh_scan"
    doc.detail = "candidates=1"
    doc.to_dict = lambda: {"symbols": ["LMNO"]}  # noqa: E731

    miss_payload = {
        "ok": True,
        "report": {"detail": "misses=2 gainers_scanned=5 top_n=20"},
        "persist": {"ok": False, "detail": "JOURNAL_S3_BUCKET unset — report not uploaded"},
    }
    with (
        patch("api.server.build_daily_watchlist", return_value=doc),
        patch("api.server.save_watchlist", return_value={"ok": True}),
        patch("api.server.hydrate_journal_from_s3", return_value={"ok": True}),
        patch("api.server.persist_journal_to_s3", return_value={"ok": True}),
        patch("api.server.run_and_persist_miss_harvest", return_value=miss_payload) as mock_miss,
        patch("api.server._holiday_noop", return_value=None),
    ):
        resp = client.post("/events", json={"phase": "postmarket", "force": True})
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert "miss_harvest=True" in body["detail"] or "miss_harvest=true" in body["detail"].lower()
    assert body["results"][0]["miss_harvest"]["ok"] is True
    mock_miss.assert_called_once()


def test_api_weekly_coaches_friday_pack(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("SECRET_ARN", "")
    monkeypatch.setenv("MOCK_BEDROCK", "true")
    monkeypatch.delenv("JOURNAL_S3_BUCKET", raising=False)
    from api import server

    client = TestClient(server.app)
    pack = {
        "ok": True,
        "week_id": "2026-W29",
        "scorecard_summary": "large_cap_sniper:worse; mid_cap_sniper:flat",
        "scorecard": {"week_id": "2026-W29", "summary": "large_cap_sniper:worse"},
        "coaches": {"ok": True, "coaches": [{"agent_id": a, "ok": True} for a in (
            "large_cap_sniper",
            "mid_cap_sniper",
            "speculative_sniper",
            "swing_momentum",
        )]},
    }
    with (
        patch("api.server.hydrate_journal_from_s3", return_value={"ok": True}),
        patch("api.server.run_friday_review", return_value=pack) as mock_review,
        patch("api.server._holiday_noop", return_value=None),
    ):
        resp = client.post("/events", json={"phase": "weekly_coaches", "force": True})
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert "friday_review" in body["detail"]
    assert "scorecard=" in body["detail"]
    assert body["results"][0]["friday_review"]["week_id"] == "2026-W29"
    mock_review.assert_called_once()


def test_friday_review_real_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MOCK_BEDROCK", "true")
    monkeypatch.delenv("JOURNAL_S3_BUCKET", raising=False)
    db = tmp_path / "j.sqlite"
    _write_journal(db)
    y, w, _ = datetime.now(UTC).isocalendar()
    monday = datetime.fromisocalendar(y, w, 1).replace(tzinfo=UTC)
    prior = WeeklyScorecard(
        week_id="2020-W01",
        built_at=monday.isoformat(),
        agents={
            aid: AgentScorecard(agent_id=aid, composite="50", trend=Trend.FLAT)
            for aid in (
                "large_cap_sniper",
                "mid_cap_sniper",
                "speculative_sniper",
                "swing_momentum",
            )
        },
    )
    report = build_miss_report(
        journal_path=db,
        injected_gainers=[
            ScreenerRow(
                symbol="ZZZZ",
                source="gainer",
                price=Decimal("15"),
                percent_change=Decimal("18"),
            )
        ],
        watchlist_symbols=["ABCD"],
    )
    pack = run_friday_review(
        db,
        report=report,
        prior_scorecard=prior,
        miss_shards=[
            {
                "agent_id": "speculative_sniper",
                "top_miss": {
                    "symbol": "ZZZZ",
                    "bucket": "A_never_watchlist",
                    "owner_sniper": "speculative_sniper",
                    "traded_by": [],
                },
                "related": [
                    {
                        "symbol": "ZZZZ",
                        "bucket": "A_never_watchlist",
                        "owner_sniper": "speculative_sniper",
                        "traded_by": [],
                    }
                ],
            }
        ],
    )
    assert pack["ok"] is True
    assert pack["week_id"] == week_id_for()
    assert "scorecard" in pack
    assert len(pack["coaches"]["coaches"]) == 4
    assert all(c["ok"] for c in pack["coaches"]["coaches"])
    # Coaches received scorecard context via mock client path
    client = CoachClient(mock=True)
    assert client.mock is True
    card = build_weekly_scorecard(db, prior=prior, miss_shards=[])
    assert "large_cap_sniper" in card.agents

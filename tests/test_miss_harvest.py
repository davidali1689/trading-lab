"""Missed-gainer harvest: A/B/C classification + per-agent top miss."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

from trading_lab.agents.sniper.shared_execution import SNIPER_SHARED
from trading_lab.improvement.coaches import run_strategy_coach, run_weekly_coaches
from trading_lab.improvement.coach_client import CoachClient
from trading_lab.improvement.miss_harvest import build_miss_report
from trading_lab.journal.sqlite import SqliteJournal
from trading_lab.market_data.alpaca_screener import ScreenerRow
from trading_lab.schemas.misses import MissBucket
from trading_lab.schemas.trades import ExitReason, RunMode, Side, SkipEvent, SkipReason, TradeRecord


def _journal(path: Path) -> SqliteJournal:
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
            setup_tags=["test"],
            entry_ts=now,
            entry_px=Decimal("100"),
            qty=Decimal("1"),
            stop_px=Decimal("98"),
            target_px=Decimal("103"),
            hold_plan=SNIPER_SHARED.default_hold_plan,
            exit_ts=now,
            exit_px=Decimal("99"),
            exit_reason=ExitReason.STOP,
            bars_held=2,
            fill_model="test",
        )
    )
    return j


def test_miss_buckets_abc(tmp_path: Path) -> None:
    db = tmp_path / "j.sqlite"
    _journal(db)
    gainers = [
        ScreenerRow(
            symbol="ZZZZ",
            source="gainer",
            price=Decimal("12"),
            volume=Decimal("2000000"),
            percent_change=Decimal("18"),
        ),
        ScreenerRow(
            symbol="ABCD",
            source="gainer",
            price=Decimal("22"),
            volume=Decimal("3000000"),
            percent_change=Decimal("12"),
        ),
        ScreenerRow(
            symbol="AAPL",
            source="gainer",
            price=Decimal("200"),
            volume=Decimal("5000000"),
            percent_change=Decimal("8"),
        ),
        ScreenerRow(
            symbol="PENY",
            source="gainer",
            price=Decimal("1.50"),
            volume=Decimal("9000000"),
            percent_change=Decimal("40"),
        ),
    ]
    report = build_miss_report(
        journal_path=db,
        injected_gainers=gainers,
        watchlist_symbols=["ABCD", "AAPL"],
        day="2026-07-17",
    )
    by_sym = {r.symbol: r for r in report.top_gainers}
    assert "PENY" not in by_sym  # penny filter
    assert by_sym["ZZZZ"].bucket == MissBucket.NEVER_WATCHLIST
    assert by_sym["ABCD"].bucket == MissBucket.WATCHED_NO_ENTER
    assert SkipReason.SETUP_MISSING.value in by_sym["ABCD"].skip_reasons
    assert by_sym["AAPL"].bucket == MissBucket.ENTERED_MISSED_MOVE
    assert report.per_agent_top_miss["speculative_sniper"] is not None
    assert report.per_agent_top_miss["speculative_sniper"].symbol == "ZZZZ"


def test_weekly_coaches_mock(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("MOCK_BEDROCK", "true")
    db = tmp_path / "j.sqlite"
    _journal(db)
    report = build_miss_report(
        journal_path=db,
        injected_gainers=[
            ScreenerRow(
                symbol="ZZZZ",
                source="gainer",
                price=Decimal("12"),
                percent_change=Decimal("15"),
            )
        ],
        watchlist_symbols=[],
    )
    client = CoachClient(mock=True)
    out = run_weekly_coaches(report=report, client=client)
    assert out["ok"] is True
    assert len(out["coaches"]) == 4
    prop = run_strategy_coach("swing_momentum", report=report, client=client, week_misses=[])
    assert prop.status == "pending_green_light"
    assert prop.mock is True

"""Missed-gainer harvest: A/B/C classification + per-agent top miss."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from trading_lab.agents.sniper.shared_execution import SNIPER_SHARED
from trading_lab.improvement.coach_client import CoachClient
from trading_lab.improvement.coaches import run_strategy_coach, run_weekly_coaches
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


def test_miss_owner_uses_resolved_market_cap(tmp_path: Path) -> None:
    """Owner sniper must use Finnhub/resolve_market_cap — not always speculative."""
    db = tmp_path / "j.sqlite"
    SqliteJournal(db)
    gainers = [
        ScreenerRow(
            symbol="MIDD",
            source="gainer",
            price=Decimal("40"),
            percent_change=Decimal("22"),
        ),
        ScreenerRow(
            symbol="HUGE",
            source="gainer",
            price=Decimal("80"),
            percent_change=Decimal("11"),
        ),
        ScreenerRow(
            symbol="TINY",
            source="gainer",
            price=Decimal("12"),
            percent_change=Decimal("30"),
        ),
    ]

    def _cap(symbol: str, explicit=None):
        caps = {
            "MIDD": Decimal("5000000000"),
            "HUGE": Decimal("50000000000"),
            "TINY": Decimal("500000000"),
        }
        return caps.get(symbol.upper())

    with patch("trading_lab.improvement.miss_harvest.resolve_market_cap", side_effect=_cap):
        report = build_miss_report(
            journal_path=db,
            injected_gainers=gainers,
            watchlist_symbols=["MIDD", "HUGE", "TINY"],
            day="2026-07-24",
        )

    by_sym = {r.symbol: r for r in report.top_gainers}
    assert by_sym["MIDD"].owner_sniper == "mid_cap_sniper"
    assert by_sym["HUGE"].owner_sniper == "large_cap_sniper"
    assert by_sym["TINY"].owner_sniper == "speculative_sniper"
    assert report.per_agent_top_miss["mid_cap_sniper"].symbol == "MIDD"
    assert report.per_agent_top_miss["large_cap_sniper"].symbol == "HUGE"
    assert report.per_agent_top_miss["speculative_sniper"].symbol == "TINY"


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

"""Journal open-trade close / ghost repair — one broker flatten ≠ N× losses."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

from trading_lab.journal.open_trades import close_journal_trade, repair_ghost_reconcile_pnl
from trading_lab.journal.sqlite import SqliteJournal
from trading_lab.schemas.hold import HoldPlan, StrategyHorizon
from trading_lab.schemas.trades import ExitReason, RunMode, Side, TradeRecord


def _open(symbol: str, *, entry: str, qty: str, when: datetime) -> TradeRecord:
    return TradeRecord(
        trade_id=uuid4(),
        run_id=uuid4(),
        found_by_agent="swing_momentum",
        symbol=symbol,
        side=Side.LONG,
        mode=RunMode.PAPER,
        setup_tags=["swing_momentum"],
        entry_ts=when,
        entry_px=Decimal(entry),
        qty=Decimal(qty),
        stop_px=Decimal(entry) * Decimal("0.97"),
        target_px=Decimal(entry) * Decimal("1.12"),
        hold_plan=HoldPlan(
            horizon=StrategyHorizon.SWING,
            min_hold_sessions=1,
            typical_hold_sessions=3,
            max_hold_sessions=10,
            summary="swing",
        ),
        exit_ts=when,
        exit_px=Decimal(entry),
        exit_reason=ExitReason.MANUAL,
        bars_held=0,
        fill_model="alpaca_paper_bracket",
        meta={"open": True, "alpaca_order_id": "x"},
    )


def test_close_journal_trade_books_only_latest_open(tmp_path: Path):
    journal = SqliteJournal(tmp_path / "j.sqlite")
    t0 = datetime(2026, 7, 1, 14, 0, tzinfo=UTC)
    for i in range(3):
        journal.write_trade(
            _open("ZYBT", entry="4.50", qty="2000", when=t0 + timedelta(hours=i))
        )

    close_journal_trade(
        tmp_path / "j.sqlite",
        "ZYBT",
        exit_px=Decimal("2.26"),
        exit_reason=ExitReason.STOP,
        closed_by="reconcile",
    )

    with sqlite3.connect(tmp_path / "j.sqlite") as conn:
        rows = list(conn.execute("SELECT pnl_usd, payload FROM trades ORDER BY entry_ts"))
    pnls = [Decimal(r[0]) for r in rows]
    # One real loss; two ghost zeros — not 3× full-qty losses.
    assert sum(1 for p in pnls if p != 0) == 1
    assert sum(pnls) == (Decimal("2.26") - Decimal("4.50")) * Decimal("2000")
    ghosts = 0
    for _pnl, payload_raw in rows:
        meta = json.loads(payload_raw)["meta"]
        if meta.get("ghost"):
            ghosts += 1
            assert meta["closed_by"] == "superseded_ghost"
    assert ghosts == 2


def test_repair_zeros_historical_reconcile_multiples(tmp_path: Path):
    journal = SqliteJournal(tmp_path / "j.sqlite")
    t0 = datetime(2026, 7, 1, 14, 0, tzinfo=UTC)
    ids = []
    for i in range(6):
        rec = _open("ZYBT", entry="4.50", qty="2250", when=t0 + timedelta(minutes=i))
        journal.write_trade(rec)
        ids.append(str(rec.trade_id))

    # Simulate the old bug: every open closed at full qty.
    exit_px = Decimal("2.259397")
    with sqlite3.connect(tmp_path / "j.sqlite") as conn:
        for tid in ids:
            row = conn.execute(
                "SELECT entry_px, qty, payload FROM trades WHERE trade_id=?", (tid,)
            ).fetchone()
            entry, qty, payload_raw = row
            payload = json.loads(payload_raw)
            entry_d = Decimal(str(entry))
            q = Decimal(str(qty))
            pnl = (exit_px - entry_d) * q
            payload["meta"] = {"open": False, "closed_by": "reconcile"}
            conn.execute(
                """
                UPDATE trades SET exit_px=?, exit_reason=?, pnl_usd=?, pnl_pct=?, payload=?
                WHERE trade_id=?
                """,
                (
                    str(exit_px),
                    "stop",
                    str(pnl),
                    str(pnl / (entry_d * q) * 100),
                    json.dumps(payload),
                    tid,
                ),
            )

    before = sum(
        Decimal(r[0])
        for r in sqlite3.connect(tmp_path / "j.sqlite")
        .execute("SELECT pnl_usd FROM trades")
        .fetchall()
    )
    assert before < Decimal("-20000")

    out = repair_ghost_reconcile_pnl(tmp_path / "j.sqlite")
    assert out["ok"] is True
    assert out["zeroed"] == 5

    after = sum(
        Decimal(r[0])
        for r in sqlite3.connect(tmp_path / "j.sqlite")
        .execute("SELECT pnl_usd FROM trades")
        .fetchall()
    )
    assert after == (exit_px - Decimal("4.50")) * Decimal("2250")

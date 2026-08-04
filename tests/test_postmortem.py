"""Post-mortem coach: digest journal + optional Bedrock narrative (never entries)."""

from __future__ import annotations

import ast
import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

from trading_lab.agents.sniper.shared_execution import SNIPER_SHARED
from trading_lab.improvement.bedrock_client import BedrockClient
from trading_lab.improvement.postmortem import (
    digest_journal,
    persist_postmortem,
    run_postmortem,
)
from trading_lab.journal.sqlite import SqliteJournal
from trading_lab.schemas.trades import ExitReason, RunMode, Side, SkipEvent, SkipReason, TradeRecord


def _write_sample_journal(path: Path) -> None:
    j = SqliteJournal(path)
    now = datetime.now(UTC)
    j.write_skip(
        SkipEvent(
            event_id=uuid4(),
            run_id=uuid4(),
            found_by_agent="large_cap_sniper",
            symbol="AAPL",
            ts=now,
            mode=RunMode.PAPER,
            skip_reason=SkipReason.SETUP_MISSING,
            detail="rvol<1.5",
        )
    )
    j.write_skip(
        SkipEvent(
            event_id=uuid4(),
            run_id=uuid4(),
            found_by_agent="large_cap_sniper",
            symbol="MSFT",
            ts=now,
            mode=RunMode.PAPER,
            skip_reason=SkipReason.INSUFFICIENT_BARS,
            detail="insufficient_bars=0",
        )
    )
    j.write_skip(
        SkipEvent(
            event_id=uuid4(),
            run_id=uuid4(),
            found_by_agent="large_cap_sniper",
            symbol="NVDA",
            ts=now,
            mode=RunMode.PAPER,
            skip_reason=SkipReason.SETUP_MISSING,
            detail="below_vwap",
        )
    )
    j.write_trade(
        TradeRecord(
            trade_id=uuid4(),
            run_id=uuid4(),
            found_by_agent="large_cap_sniper",
            symbol="SPY",
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
            exit_px=Decimal("102"),
            exit_reason=ExitReason.TARGET,
            bars_held=3,
            fill_model="test",
        )
    )


def test_digest_aggregates_skips_and_trades(tmp_path: Path) -> None:
    path = tmp_path / "journal.sqlite"
    _write_sample_journal(path)
    digest = digest_journal(path)
    assert digest["trade_count"] == 1
    assert digest["skip_count"] == 3
    assert digest["skips_by_reason"]["setup_missing"] == 2
    assert digest["skips_by_reason"]["insufficient_bars"] == 1
    assert digest["trades_by_agent"]["large_cap_sniper"] == 1
    assert digest["pnl_usd_total"] == "2.00"
    assert "SPY" in digest["symbols_traded"]


def test_bedrock_mock_returns_stub(monkeypatch) -> None:
    monkeypatch.setenv("MOCK_BEDROCK", "true")
    monkeypatch.setenv("BEDROCK_MODEL_ID", "amazon.nova-lite-v1:0")
    client = BedrockClient()
    text = client.converse("sys", "hello digest")
    assert "[MOCK]" in text
    assert "amazon.nova-lite-v1:0" in text


def test_run_postmortem_includes_digest_and_narrative(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("MOCK_BEDROCK", "true")
    path = tmp_path / "journal.sqlite"
    _write_sample_journal(path)
    report = run_postmortem(path)
    assert report["ok"] is True
    assert report["digest"]["skip_count"] == 3
    assert "[MOCK]" in report["narrative"]
    assert report["mock"] is True


def test_persist_postmortem_uploads_dated(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("JOURNAL_S3_BUCKET", "bucket")
    report = {"ok": True, "digest": {"trade_count": 0}, "narrative": "n", "mock": True}
    uploaded: dict[str, bytes] = {}

    class FakeS3:
        def put_object(self, *, Bucket, Key, Body, ContentType=None):  # noqa: N803
            uploaded[Key] = Body if isinstance(Body, bytes) else Body.encode("utf-8")

    from unittest.mock import patch

    with patch("boto3.client", return_value=FakeS3()):
        out = persist_postmortem(report, day="2026-07-15")
    assert out["ok"] is True
    assert "journals/2026-07-15/postmortem.json" in uploaded
    body = json.loads(uploaded["journals/2026-07-15/postmortem.json"])
    assert body["digest"]["trade_count"] == 0


def test_tick_path_does_not_import_postmortem() -> None:
    """Entry path must stay free of coach imports."""
    root = Path(__file__).resolve().parents[1]
    paper_tick = (root / "src" / "trading_lab" / "pipeline" / "paper_tick.py").read_text(
        encoding="utf-8"
    )
    tree = ast.parse(paper_tick)
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported.add(alias.name)
    assert not any("postmortem" in m or "bedrock" in m for m in imported)

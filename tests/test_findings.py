"""Findings in the journal bucket change the paper tick. They do not bypass it."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import MagicMock, patch

from trading_lab.agents.sniper.decision import SniperDecision
from trading_lab.agents.sniper.shared_execution import SniperStatus
from trading_lab.broker.types import BrokerAccount
from trading_lab.journal.findings import (
    finding_blocks_trade,
    finding_has_catalyst,
    load_premarket_finding,
    upload_premarket_finding,
)
from trading_lab.market_data.types import Bar
from trading_lab.pipeline.paper_agents import run_sniper_paper_tick


class _Body:
    def __init__(self, raw: bytes) -> None:
        self._raw = raw

    def read(self) -> bytes:
        return self._raw


class FakeS3:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    def put_object(self, *, Bucket: str, Key: str, Body: bytes, ContentType: str) -> None:
        self.objects[Key] = Body

    def get_object(self, *, Bucket: str, Key: str) -> dict:
        if Key not in self.objects:
            raise FileNotFoundError(Key)
        return {"Body": _Body(self.objects[Key])}


def test_upload_then_load_roundtrip() -> None:
    fake = FakeS3()
    uploaded = upload_premarket_finding(
        {
            "day": "2026-10-22",
            "symbol": "aapl",
            "catalyst_score": 2,
            "risk_score": 0,
        },
        bucket="lab-journal",
        client=fake,
    )
    assert uploaded["ok"] is True
    assert uploaded["key"] == "findings/premarket/2026-10-22/AAPL.json"
    loaded = load_premarket_finding(
        "AAPL", day="2026-10-22", bucket="lab-journal", client=fake
    )
    assert loaded is not None
    assert loaded["catalyst_score"] == 2
    assert finding_has_catalyst(loaded)
    assert not finding_blocks_trade(loaded)


def test_upload_refuses_without_bucket(monkeypatch) -> None:
    monkeypatch.delenv("JOURNAL_S3_BUCKET", raising=False)
    out = upload_premarket_finding({"symbol": "AAPL", "catalyst_score": 1})
    assert out["ok"] is False
    assert "JOURNAL_S3_BUCKET" in out["detail"]


def _bars(symbol: str) -> list[Bar]:
    return [
        Bar(
            symbol=symbol,
            ts=datetime(2026, 10, 22, 14, i, tzinfo=UTC),
            open=Decimal("100"),
            high=Decimal("101"),
            low=Decimal("99"),
            close=Decimal("100"),
            volume=Decimal("10000"),
            timeframe="1Min",
        )
        for i in range(25)
    ]


def _broker() -> MagicMock:
    broker = MagicMock()
    broker.get_account.return_value = BrokerAccount(
        equity=Decimal("100000"),
        cash=Decimal("100000"),
        buying_power=Decimal("200000"),
        paper=True,
    )
    broker.get_open_positions.return_value = []
    broker.has_open_position.return_value = False
    return broker


def test_risk_finding_blocks_order(tmp_path) -> None:
    broker = _broker()
    md = MagicMock()
    md.get_bars.return_value = _bars("AAPL")
    with (
        patch("trading_lab.pipeline.paper_agents.resolve_market_data", return_value=md),
        patch("trading_lab.pipeline.paper_agents.AlpacaPaperBroker", return_value=broker),
        patch(
            "trading_lab.pipeline.paper_agents.load_premarket_finding",
            return_value={"symbol": "AAPL", "catalyst_score": 2, "risk_score": 2},
        ),
    ):
        out = run_sniper_paper_tick(
            symbol="AAPL",
            journal_path=str(tmp_path / "j.sqlite"),
            agent_id="large_cap_sniper",
            market_cap_usd=Decimal("3000000000000"),
            broker=broker,
        )
    assert out["detail"] == "finding_risk"
    assert out["orders"] == 0
    broker.submit_bracket_order.assert_not_called()


def test_catalyst_finding_reaches_checklist(tmp_path) -> None:
    """A catalyst score is the news check. The checklist still decides the order."""
    broker = _broker()
    md = MagicMock()
    md.get_bars.return_value = _bars("AAPL")
    seen: dict = {}

    def _eval(ctx, mode):  # noqa: ANN001
        seen["has_catalyst"] = ctx.has_catalyst
        return SniperDecision(
            agent_id="large_cap_sniper",
            symbol="AAPL",
            status=SniperStatus.NO_TRADE,
            reason="rvol",
        )

    with (
        patch("trading_lab.pipeline.paper_agents.resolve_market_data", return_value=md),
        patch("trading_lab.pipeline.paper_agents.AlpacaPaperBroker", return_value=broker),
        patch("trading_lab.pipeline.paper_agents._paper_has_catalyst", return_value=False),
        patch(
            "trading_lab.pipeline.paper_agents.load_premarket_finding",
            return_value={"symbol": "AAPL", "catalyst_score": 2, "risk_score": 0},
        ),
        patch("trading_lab.pipeline.paper_agents.evaluate_large_cap_sniper", side_effect=_eval),
    ):
        out = run_sniper_paper_tick(
            symbol="AAPL",
            journal_path=str(tmp_path / "j.sqlite"),
            agent_id="large_cap_sniper",
            market_cap_usd=Decimal("3000000000000"),
            broker=broker,
        )
    assert seen["has_catalyst"] is True
    assert out["orders"] == 0
    broker.submit_bracket_order.assert_not_called()
    json.dumps(out)

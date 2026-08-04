"""E2E day lifecycle — 2026-08-04 remediation confirmation.

Simulates a full session with a fake broker + synthetic bars:
watchlist floor → sniper ENTER → repeat-entry guard → bracket-leg fail-safe →
exit-reassess retry → journal prune → miss-harvest capture in scorecard.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest

from trading_lab.broker.alpaca import AlpacaPaperBroker
from trading_lab.broker.types import BrokerAccount, BrokerOrderResult, BrokerPosition
from trading_lab.execution.risk_gate import RiskGate, RiskGateConfig
from trading_lab.improvement.scorecard import build_weekly_scorecard, week_id_for
from trading_lab.journal.persist import prune_journal
from trading_lab.journal.sqlite import SqliteJournal
from trading_lab.market_data.types import Bar
from trading_lab.pipeline import paper_agents
from trading_lab.pipeline.exit_reassess import _close_position_retry, reassess_open_exits
from trading_lab.pipeline.paper_agents import (
    resolve_market_cap,
    resolve_sniper_agent,
    run_sniper_paper_tick,
)
from trading_lab.schemas.trades import RunMode, SkipEvent, SkipReason
from trading_lab.selection.watchlist import build_daily_watchlist

ET = ZoneInfo("America/New_York")
FIXED_NOW_ET = datetime(2026, 8, 4, 10, 30, tzinfo=ET)
BAR_DAY = datetime(2026, 8, 4, 13, 30, tzinfo=UTC)  # 09:30 ET


class DayMarketData:
    """1Min bars with a last-bar volume spike (RVOL >> 4); rising 1Day bars."""

    def get_bars(self, request) -> list[Bar]:
        bars: list[Bar] = []
        if "Min" in request.timeframe:
            px = Decimal("100")
            for i in range(30):
                ts = BAR_DAY + timedelta(minutes=i)
                vol = Decimal("1500000") if i == 29 else Decimal("100000")
                bars.append(
                    Bar(
                        symbol=request.symbol,
                        ts=ts,
                        open=px,
                        high=px + Decimal("0.2"),
                        low=px - Decimal("0.05"),
                        close=px + Decimal("0.1"),
                        volume=vol,
                        vwap=px + Decimal("0.05"),
                        timeframe=request.timeframe,
                    )
                )
                px += Decimal("0.1")
        else:
            px = Decimal("90")
            for i in range(30):
                ts = BAR_DAY - timedelta(days=30 - i)
                bars.append(
                    Bar(
                        symbol=request.symbol,
                        ts=ts,
                        open=px,
                        high=px + Decimal("0.5"),
                        low=px - Decimal("0.5"),
                        close=px + Decimal("0.6"),
                        volume=Decimal("5000000"),
                        vwap=px,
                        timeframe=request.timeframe,
                    )
                )
                px += Decimal("0.6")
        return bars


class FakeBroker(AlpacaPaperBroker):
    """Paper broker double: no HTTP; configurable legs + close failures."""

    def __init__(self, *, legs: list[dict] | None = None, close_failures: int = 0) -> None:
        self.legs = legs
        self.positions: list[BrokerPosition] = []
        self.close_calls: list[tuple[str, Decimal | None]] = []
        self.cancel_calls = 0
        self.submitted: list = []
        self._close_failures = close_failures

    def get_account(self) -> BrokerAccount:
        return BrokerAccount(
            equity=Decimal("100000"),
            cash=Decimal("80000"),
            buying_power=Decimal("80000"),
            paper=True,
            settled_cash=Decimal("80000"),
        )

    def get_open_positions(self) -> list[BrokerPosition]:
        return self.positions

    def has_open_position(self, symbol: str) -> bool:
        return any(p.symbol.upper() == symbol.upper() for p in self.positions)

    def list_open_orders(self, symbol: str | None = None) -> list[dict]:
        return []

    def cancel_open_orders(self, symbol: str) -> list:
        self.cancel_calls += 1
        return []

    def submit_bracket_order(self, intent) -> BrokerOrderResult:
        self.submitted.append(intent)
        return BrokerOrderResult(
            order_id="fake-order-1",
            symbol=intent.symbol,
            status="filled",
            qty=intent.qty,
            raw={},
            filled_avg_price=intent.entry_px,
        )

    def wait_for_fill(self, order_id: str, **kwargs) -> BrokerOrderResult:
        intent = self.submitted[-1]
        return BrokerOrderResult(
            order_id=order_id,
            symbol=intent.symbol,
            status="filled",
            qty=intent.qty,
            raw={},
            filled_avg_price=intent.entry_px,
        )

    def get_order(self, order_id: str) -> dict:
        if self.legs is None:
            return {"id": order_id, "status": "filled"}
        return {"id": order_id, "status": "filled", "legs": self.legs}

    def close_position(self, symbol: str, qty: Decimal | None = None) -> dict:
        self.close_calls.append((symbol, qty))
        if self._close_failures > 0:
            self._close_failures -= 1
            raise RuntimeError(
                "Alpaca DELETE /v2/positions/X HTTP 403: "
                '{"message":"insufficient qty available for order","available":"0"}'
            )
        self.positions = [p for p in self.positions if p.symbol.upper() != symbol.upper()]
        return {"ok": True}


def _tick(symbol: str, broker: FakeBroker, journal_path: str) -> dict:
    with (
        patch.object(paper_agents, "resolve_market_data", return_value=DayMarketData()),
        patch.object(paper_agents, "_paper_has_catalyst", return_value=True),
        patch.object(paper_agents, "now_et", return_value=FIXED_NOW_ET),
    ):
        return run_sniper_paper_tick(
            symbol=symbol,
            journal_path=journal_path,
            agent_id="speculative_sniper",
            market_cap_usd=None,
            broker=broker,
        )


def test_e2e_day_lifecycle(tmp_path: Path) -> None:
    journal = str(tmp_path / "journal.sqlite")

    # 1. Watchlist: null-price actives resolved; sub-$5 rejected, >=$5 kept.
    screener = MagicMock()
    screener.most_actives.return_value = [
        SimpleNamespace(symbol="ENSC", source="most_actives", price=None,
                        volume=Decimal("122048515"), percent_change=None),
        SimpleNamespace(symbol="UPC", source="most_actives", price=None,
                        volume=Decimal("90000000"), percent_change=None),
    ]
    screener.movers.return_value = []
    screener.asset.side_effect = lambda sym: SimpleNamespace(
        symbol=sym, tradable=True, status="active", asset_class="us_equity", exchange="NASDAQ"
    )
    screener.last_trade_price.side_effect = lambda sym: (
        Decimal("0.43") if sym == "ENSC" else Decimal("6.48")
    )
    doc = build_daily_watchlist(screener=screener, size=12, verify_assets=True)
    assert "ENSC" not in doc.symbols  # penny floor no longer bypassed by null price
    assert "UPC" in doc.symbols

    # 2. First tick on a watchlisted name → ENTER with verified stop leg.
    good_legs = [
        {"side": "sell", "type": "limit"},
        {"side": "sell", "type": "stop"},
    ]
    broker = FakeBroker(legs=good_legs)
    out = _tick("UPC", broker, journal)
    assert out["status"] == "ORDER_SUBMITTED", out
    assert out["orders"] == 1
    db = SqliteJournal(journal)
    assert db.count_symbol_entries_since("UPC", "speculative_sniper", "2000-01-01") == 1

    # 3. Repeat tick same session → guard blocks (ENSC 3x / ZYBT 6x regression).
    out2 = _tick("UPC", broker, journal)
    assert out2["status"] == "SKIP"
    assert out2["detail"] == "repeat_entry_symbol_day"
    assert db.count_symbol_entries_since("UPC", "speculative_sniper", "2000-01-01") == 1

    # 4. Bracket without a stop leg → fail-safe flatten, journal closed.
    naked = FakeBroker(legs=[{"side": "sell", "type": "limit"}])
    out3 = _tick("CWVX", naked, journal)
    assert out3["status"] == "STOP_LEG_MISSING_FLATTENED"
    assert naked.close_calls == [("CWVX", None)]

    # 5. Exit reassess: close races held_for_orders → cancel + retry succeeds.
    flaky = FakeBroker(close_failures=1)
    flaky.positions = [
        BrokerPosition(
            symbol="AAPL",
            qty=Decimal("51"),
            side="long",
            market_value=Decimal("10000"),
            unrealized_pl=Decimal("50"),
            avg_entry_price=Decimal("200"),
            current_price=Decimal("201"),
        )
    ]
    actions = reassess_open_exits(journal, broker=flaky, outside_rth=False)
    orphan = [a for a in actions if a.get("action") == "flatten_orphan"]
    assert orphan and orphan[0]["ok"] is True, actions
    assert flaky.cancel_calls >= 1
    assert flaky.close_calls[0] == ("AAPL", None)

    # 6. Persist prune: old skips dropped, recent kept, trades untouched.
    j = SqliteJournal(journal)
    old_ts = datetime.now(UTC) - timedelta(days=30)
    for i in range(5):
        j.write_skip(
            SkipEvent(
                event_id=uuid4(),
                run_id=uuid4(),
                found_by_agent="speculative_sniper",
                symbol="OLD",
                ts=old_ts,
                mode=RunMode.PAPER,
                skip_reason=SkipReason.SETUP_MISSING,
                detail="old",
            )
        )
    before = j.count_symbol_entries_since("UPC", "speculative_sniper", "2000-01-01")
    prune = prune_journal(journal, keep_days=3)
    assert prune["pruned_skips"] == 5
    assert j.count_symbol_entries_since("UPC", "speculative_sniper", "2000-01-01") == before

    # 7. Scorecard: bucket-C gainer with positive P&L counts as captured.
    shards = [
        {
            "agent_id": "speculative_sniper",
            "related": [
                {
                    "symbol": "DFNS",
                    "bucket": "C_entered_missed_move",
                    "owner_sniper": "speculative_sniper",
                    "traded_by": ["speculative_sniper"],
                    "trade_pnl_pct": "5.67",
                }
            ],
        }
    ]
    card = build_weekly_scorecard(
        journal, week_id=week_id_for(), miss_shards=shards, prior=None
    )
    spec = card.agents["speculative_sniper"]
    assert spec.gainer_opportunities == 1
    assert spec.gainers_captured == 1


def test_late_speculative_entry_blocked(tmp_path: Path) -> None:
    journal = str(tmp_path / "journal.sqlite")
    broker = FakeBroker(legs=[])
    with (
        patch.object(paper_agents, "resolve_market_data", return_value=DayMarketData()),
        patch.object(paper_agents, "_paper_has_catalyst", return_value=True),
        patch.object(
            paper_agents, "now_et", return_value=datetime(2026, 8, 4, 15, 45, tzinfo=ET)
        ),
    ):
        out = run_sniper_paper_tick(
            symbol="UPC",
            journal_path=journal,
            agent_id="speculative_sniper",
            market_cap_usd=None,
            broker=broker,
        )
    assert out["status"] == "SKIP"
    assert out["detail"] == "speculative_late_first_entry"


def test_daily_loss_includes_unrealized() -> None:
    gate = RiskGate(config=RiskGateConfig(max_daily_loss_usd=Decimal("500")))
    gate.state.open_unrealized_pl = Decimal("-600")
    intent = SimpleNamespace(entry_px=Decimal("10"), qty=Decimal("1"))
    decision = gate.check(intent, FIXED_NOW_ET)  # type: ignore[arg-type]
    assert decision.allowed is False
    assert decision.skip_reason == SkipReason.DAILY_LOSS_HIT


def test_static_cap_routing() -> None:
    assert resolve_sniper_agent(None, "INTC") == "large_cap_sniper"
    assert resolve_sniper_agent(None, "PLTR") == "large_cap_sniper"
    assert resolve_sniper_agent(None, "AAL") == "mid_cap_sniper"
    assert resolve_sniper_agent(None, "ZZZZ") == "speculative_sniper"
    assert resolve_market_cap("AAL") == Decimal("5000000000")


def test_close_position_retry_exhausts() -> None:
    broker = FakeBroker(close_failures=5)
    with pytest.raises(RuntimeError, match="insufficient qty"):
        _close_position_retry(broker, "NVDA", attempts=3, wait_sec=0.01)
    assert broker.cancel_calls == 3


def test_coach_converse_retries_with_us_prefix(monkeypatch: pytest.MonkeyPatch) -> None:
    from trading_lab.improvement.coach_client import CoachClient

    calls: list[str] = []

    class FakeBedrock:
        def converse(self, *, modelId, messages, inferenceConfig):  # noqa: N803
            calls.append(modelId)
            if not modelId.startswith("us."):
                raise RuntimeError("ValidationException: The provided model identifier is invalid.")
            return {"output": {"message": {"content": [{"text": "ok"}]}}}

    fake_boto3 = MagicMock()
    fake_boto3.client.return_value = FakeBedrock()
    monkeypatch.setitem(__import__("sys").modules, "boto3", fake_boto3)

    client = CoachClient(model_id="moonshot.kimi-k2-thinking", mock=False)
    assert client.analyze("sys", "user") == "ok"
    assert calls == ["moonshot.kimi-k2-thinking", "us.moonshot.kimi-k2-thinking"]

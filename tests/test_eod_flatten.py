"""EOD flatten closes sniper opens only — swing may overnight."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock, patch
from uuid import uuid4

from trading_lab.agents.sniper.shared_execution import SNIPER_SHARED
from trading_lab.journal.sqlite import SqliteJournal
from trading_lab.pipeline.eod_flatten import flatten_sniper_paper
from trading_lab.schemas.hold import HoldPlan, StrategyHorizon
from trading_lab.schemas.trades import ExitReason, RunMode, Side, TradeRecord


def _open_trade(*, agent: str, symbol: str) -> TradeRecord:
    now = datetime.now(UTC)
    hold = (
        SNIPER_SHARED.default_hold_plan
        if agent != "swing_momentum"
        else HoldPlan(
            horizon=StrategyHorizon.SWING,
            min_hold_sessions=1,
            typical_hold_sessions=3,
            max_hold_sessions=10,
            summary="swing overnight ok",
        )
    )
    return TradeRecord(
        trade_id=uuid4(),
        run_id=uuid4(),
        found_by_agent=agent,
        symbol=symbol,
        side=Side.LONG,
        mode=RunMode.PAPER,
        setup_tags=[agent],
        entry_ts=now,
        entry_px=Decimal("10"),
        qty=Decimal("1"),
        stop_px=Decimal("9"),
        target_px=Decimal("11"),
        hold_plan=hold,
        exit_ts=now,
        exit_px=Decimal("10"),
        exit_reason=ExitReason.MANUAL,
        bars_held=0,
        fill_model="alpaca_paper_bracket",
        meta={"open": True, "alpaca_order_id": "x"},
    )


def test_flatten_closes_sniper_not_swing(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("ALPACA_API_KEY", "PK")
    monkeypatch.setenv("ALPACA_API_SECRET", "SK")
    journal = SqliteJournal(tmp_path / "j.sqlite")
    journal.write_trade(_open_trade(agent="speculative_sniper", symbol="ELVA"))
    journal.write_trade(_open_trade(agent="swing_momentum", symbol="AAL"))

    broker = MagicMock()
    broker.close_position.return_value = {"symbol": "ELVA", "status": "closed"}

    with patch("trading_lab.pipeline.eod_flatten.AlpacaPaperBroker", return_value=broker):
        out = flatten_sniper_paper(str(tmp_path / "j.sqlite"))

    assert any(r.get("symbol") == "ELVA" and r.get("ok") is True for r in out)
    broker.close_position.assert_called_once_with("ELVA")


def test_flatten_noop_when_no_open_snipers(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("ALPACA_API_KEY", "PK")
    monkeypatch.setenv("ALPACA_API_SECRET", "SK")
    journal = SqliteJournal(tmp_path / "j.sqlite")
    journal.write_trade(_open_trade(agent="swing_momentum", symbol="AAL"))

    broker = MagicMock()
    with patch("trading_lab.pipeline.eod_flatten.AlpacaPaperBroker", return_value=broker):
        out = flatten_sniper_paper(str(tmp_path / "j.sqlite"))

    assert out == [{"ok": True, "detail": "no_open_sniper_positions"}]
    broker.close_position.assert_not_called()

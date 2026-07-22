"""Daily exit reassessment — open positions must never sit naked after day brackets expire."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock, patch
from uuid import uuid4

from trading_lab.broker.types import BrokerAccount, BrokerOrderResult, BrokerPosition
from trading_lab.journal.sqlite import SqliteJournal
from trading_lab.pipeline.exit_reassess import (
    ExitAction,
    assess_exit_action,
    reassess_open_exits,
)
from trading_lab.schemas.hold import HoldPlan, StrategyHorizon
from trading_lab.schemas.trades import ExitReason, RunMode, Side, TradeIntent, TradeRecord


def test_assess_past_target_flattens():
    action = assess_exit_action(
        entry=Decimal("10.49"),
        mark=Decimal("12.20"),
        stop=Decimal("10.17"),
        target=Decimal("11.74"),
        scale_gain_pct=Decimal("4"),
    )
    assert action == ExitAction.FLATTEN_TARGET


def test_assess_at_or_below_stop_flattens():
    action = assess_exit_action(
        entry=Decimal("10.49"),
        mark=Decimal("10.10"),
        stop=Decimal("10.17"),
        target=Decimal("11.74"),
    )
    assert action == ExitAction.FLATTEN_STOP


def test_assess_below_scale_rearms_oco():
    action = assess_exit_action(
        entry=Decimal("10.49"),
        mark=Decimal("10.70"),
        stop=Decimal("10.17"),
        target=Decimal("11.74"),
        scale_gain_pct=Decimal("4"),
    )
    assert action == ExitAction.REARM_OCO


def test_assess_between_scale_and_target_scales():
    # +4% of 10.49 = 10.9096; mark 11.20 is between scale and 11.74 target
    action = assess_exit_action(
        entry=Decimal("10.49"),
        mark=Decimal("11.20"),
        stop=Decimal("10.17"),
        target=Decimal("11.74"),
        scale_gain_pct=Decimal("4"),
    )
    assert action == ExitAction.SCALE_AND_TRAIL


def test_swing_bracket_uses_gtc(monkeypatch):
    from trading_lab.broker.alpaca import AlpacaPaperBroker

    monkeypatch.setenv("ALPACA_API_KEY", "PK")
    monkeypatch.setenv("ALPACA_API_SECRET", "SK")
    monkeypatch.setenv("ALPACA_PAPER", "true")

    broker = AlpacaPaperBroker(
        api_key="PK", api_secret="SK", base_url="https://paper-api.alpaca.markets"
    )
    intent = TradeIntent(
        found_by_agent="swing_momentum",
        symbol="CIFG",
        side=Side.LONG,
        entry_px=Decimal("10.49"),
        stop_px=Decimal("10.17"),
        target_px=Decimal("11.74"),
        qty=Decimal("100"),
        hold_plan=HoldPlan(
            horizon=StrategyHorizon.SWING,
            min_hold_sessions=1,
            typical_hold_sessions=3,
            max_hold_sessions=10,
            summary="swing",
        ),
    )
    with patch.object(
        broker,
        "_request",
        return_value={"id": "ord-swing", "symbol": "CIFG", "status": "accepted", "qty": "100"},
    ) as req:
        broker.submit_bracket_order(intent)
    assert req.call_args[0][2]["time_in_force"] == "gtc"


def test_sniper_bracket_stays_day(monkeypatch):
    from trading_lab.broker.alpaca import AlpacaPaperBroker

    monkeypatch.setenv("ALPACA_API_KEY", "PK")
    monkeypatch.setenv("ALPACA_API_SECRET", "SK")
    broker = AlpacaPaperBroker(
        api_key="PK", api_secret="SK", base_url="https://paper-api.alpaca.markets"
    )
    intent = TradeIntent(
        found_by_agent="large_cap_sniper",
        symbol="AAPL",
        side=Side.LONG,
        entry_px=Decimal("190"),
        stop_px=Decimal("186"),
        target_px=Decimal("196"),
        qty=Decimal("5"),
        hold_plan=HoldPlan(
            horizon=StrategyHorizon.INTRADAY,
            min_hold_sessions=0,
            typical_hold_sessions=0,
            max_hold_sessions=0,
            summary="intraday",
        ),
    )
    with patch.object(
        broker,
        "_request",
        return_value={"id": "ord-1", "symbol": "AAPL", "status": "accepted", "qty": "5"},
    ) as req:
        broker.submit_bracket_order(intent)
    assert req.call_args[0][2]["time_in_force"] == "day"


def _open_swing(symbol: str, *, entry: str, stop: str, target: str, qty: str = "100") -> TradeRecord:
    now = datetime.now(UTC)
    return TradeRecord(
        trade_id=uuid4(),
        run_id=uuid4(),
        found_by_agent="swing_momentum",
        symbol=symbol,
        side=Side.LONG,
        mode=RunMode.PAPER,
        setup_tags=["swing_momentum"],
        entry_ts=now,
        entry_px=Decimal(entry),
        qty=Decimal(qty),
        stop_px=Decimal(stop),
        target_px=Decimal(target),
        hold_plan=HoldPlan(
            horizon=StrategyHorizon.SWING,
            min_hold_sessions=1,
            typical_hold_sessions=3,
            max_hold_sessions=10,
            summary="swing",
        ),
        exit_ts=now,
        exit_px=Decimal(entry),
        exit_reason=ExitReason.MANUAL,
        bars_held=0,
        fill_model="alpaca_paper_bracket",
        meta={"open": True, "alpaca_order_id": "x"},
    )


def test_reassess_past_target_closes_when_naked(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("ALPACA_API_KEY", "PK")
    monkeypatch.setenv("ALPACA_API_SECRET", "SK")
    journal = SqliteJournal(tmp_path / "j.sqlite")
    journal.write_trade(
        _open_swing("CIFG", entry="10.49", stop="10.17", target="11.74", qty="1921")
    )

    broker = MagicMock()
    broker.get_account.return_value = BrokerAccount(
        equity=Decimal("100000"), cash=Decimal("50000"), buying_power=Decimal("100000")
    )
    broker.get_open_positions.return_value = [
        BrokerPosition(
            symbol="CIFG",
            qty=Decimal("1921"),
            side="long",
            avg_entry_price=Decimal("10.49"),
            current_price=Decimal("12.20"),
            market_value=Decimal("23436"),
            unrealized_pl=Decimal("3284"),
        )
    ]
    broker.list_open_orders.return_value = []
    broker.close_position.return_value = {"symbol": "CIFG", "status": "closed"}

    with patch("trading_lab.pipeline.exit_reassess.AlpacaPaperBroker", return_value=broker):
        out = reassess_open_exits(str(tmp_path / "j.sqlite"))

    assert any(r.get("symbol") == "CIFG" and r.get("action") == "flatten_target" for r in out)
    broker.close_position.assert_called_once_with("CIFG")


def test_reassess_below_scale_rearms_gtc_oco_when_naked(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("ALPACA_API_KEY", "PK")
    monkeypatch.setenv("ALPACA_API_SECRET", "SK")
    journal = SqliteJournal(tmp_path / "j.sqlite")
    journal.write_trade(_open_swing("IREG", entry="9.39", stop="9.11", target="10.52", qty="2155"))

    broker = MagicMock()
    broker.get_open_positions.return_value = [
        BrokerPosition(
            symbol="IREG",
            qty=Decimal("2155"),
            side="long",
            avg_entry_price=Decimal("9.39"),
            current_price=Decimal("9.55"),
        )
    ]
    broker.list_open_orders.return_value = []
    broker.submit_oco_exit.return_value = BrokerOrderResult(
        order_id="oco-1", symbol="IREG", status="accepted", qty=Decimal("2155")
    )

    with patch("trading_lab.pipeline.exit_reassess.AlpacaPaperBroker", return_value=broker):
        out = reassess_open_exits(str(tmp_path / "j.sqlite"))

    assert any(r.get("symbol") == "IREG" and r.get("action") == "rearm_oco" for r in out)
    broker.submit_oco_exit.assert_called_once()
    kwargs = broker.submit_oco_exit.call_args.kwargs
    assert kwargs["symbol"] == "IREG"
    assert kwargs["qty"] == Decimal("2155")
    assert kwargs["stop_px"] == Decimal("9.11")
    assert kwargs["target_px"] == Decimal("10.52")
    assert kwargs["time_in_force"] == "gtc"


def test_reassess_noop_when_exit_orders_already_resting(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("ALPACA_API_KEY", "PK")
    monkeypatch.setenv("ALPACA_API_SECRET", "SK")
    journal = SqliteJournal(tmp_path / "j.sqlite")
    journal.write_trade(_open_swing("IREX", entry="17.16", stop="16.64", target="19.21", qty="1165"))

    broker = MagicMock()
    broker.get_open_positions.return_value = [
        BrokerPosition(
            symbol="IREX",
            qty=Decimal("1165"),
            side="long",
            avg_entry_price=Decimal("17.16"),
            current_price=Decimal("17.35"),
        )
    ]
    broker.list_open_orders.return_value = [
        {"symbol": "IREX", "side": "sell", "status": "new", "type": "limit"}
    ]

    with patch("trading_lab.pipeline.exit_reassess.AlpacaPaperBroker", return_value=broker):
        out = reassess_open_exits(str(tmp_path / "j.sqlite"))

    row = next(r for r in out if r.get("symbol") == "IREX")
    assert row["action"] == "noop_has_exits"
    broker.submit_oco_exit.assert_not_called()
    broker.close_position.assert_not_called()

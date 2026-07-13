"""Tests for Alpaca paper broker + paper tick path."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import MagicMock, patch

from trading_lab.agents.sniper.decision import SniperDecision, TradeMap
from trading_lab.agents.sniper.shared_execution import SniperStatus
from trading_lab.broker.alpaca import AlpacaPaperBroker
from trading_lab.broker.types import BrokerAccount, BrokerOrderResult
from trading_lab.market_data.factory import resolve_market_data, use_mock_bars
from trading_lab.market_data.mock import MockMarketData
from trading_lab.market_data.types import Bar
from trading_lab.pipeline.paper_tick import _qty_for_price, run_paper_tick
from trading_lab.schemas.hold import HoldPlan, StrategyHorizon
from trading_lab.schemas.trades import Side, TradeIntent


def test_use_mock_bars_default(monkeypatch):
    monkeypatch.delenv("USE_MOCK_BARS", raising=False)
    assert use_mock_bars() is True
    assert isinstance(resolve_market_data(), MockMarketData)


def test_qty_for_100k_style_notional():
    assert _qty_for_price(Decimal("100"), Decimal("1000")) == Decimal("10")
    assert _qty_for_price(Decimal("250"), Decimal("1000")) == Decimal("4")


def test_broker_submit_bracket(monkeypatch):
    monkeypatch.setenv("ALPACA_API_KEY", "PK")
    monkeypatch.setenv("ALPACA_API_SECRET", "SK")
    monkeypatch.setenv("ALPACA_PAPER", "true")

    broker = AlpacaPaperBroker(api_key="PK", api_secret="SK", base_url="https://paper-api.alpaca.markets")
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
            max_hold_sessions=1,
            summary="intraday",
        ),
    )

    with patch.object(
        broker,
        "_request",
        return_value={"id": "ord-1", "symbol": "AAPL", "status": "accepted", "qty": "5"},
    ) as req:
        result = broker.submit_bracket_order(intent)
    assert result.order_id == "ord-1"
    assert req.call_args[0][0] == "POST"
    body = req.call_args[0][2]
    assert body["order_class"] == "bracket"
    assert body["time_in_force"] == "day"


def test_paper_tick_no_enter_skips_broker(tmp_path, monkeypatch):
    monkeypatch.setenv("USE_MOCK_BARS", "false")
    monkeypatch.setenv("ALPACA_API_KEY", "PK")
    monkeypatch.setenv("ALPACA_API_SECRET", "SK")

    bars = [
        Bar(
            symbol="AAPL",
            ts=datetime(2026, 7, 13, 14, i, tzinfo=UTC),
            open=Decimal("100"),
            high=Decimal("101"),
            low=Decimal("99"),
            close=Decimal("100"),
            volume=Decimal("1000"),
            timeframe="1Min",
        )
        for i in range(25)
    ]
    md = MagicMock()
    md.get_bars.return_value = bars
    broker = MagicMock()
    broker.get_account.return_value = BrokerAccount(
        equity=Decimal("100000"),
        cash=Decimal("100000"),
        buying_power=Decimal("200000"),
        paper=True,
    )
    broker.get_open_positions.return_value = []
    broker.has_open_position.return_value = False

    decision = SniperDecision(
        agent_id="large_cap_sniper",
        symbol="AAPL",
        status=SniperStatus.NO_TRADE,
        reason="rvol_low",
    )

    with (
        patch("trading_lab.pipeline.paper_tick.resolve_market_data", return_value=md),
        patch("trading_lab.pipeline.paper_tick.AlpacaPaperBroker", return_value=broker),
        patch("trading_lab.pipeline.paper_tick.evaluate_large_cap_sniper", return_value=decision),
    ):
        out = run_paper_tick(symbol="AAPL", journal_path=str(tmp_path / "j.sqlite"))

    assert out["orders"] == 0
    assert out["status"] == "NO_TRADE"
    broker.submit_bracket_order.assert_not_called()


def test_paper_tick_enter_submits(tmp_path, monkeypatch):
    monkeypatch.setenv("USE_MOCK_BARS", "false")
    bars = [
        Bar(
            symbol="AAPL",
            ts=datetime(2026, 7, 13, 14, i, tzinfo=UTC),
            open=Decimal("190"),
            high=Decimal("191"),
            low=Decimal("189"),
            close=Decimal("190"),
            volume=Decimal("2000000"),
            vwap=Decimal("189"),
            timeframe="1Min",
        )
        for i in range(25)
    ]
    md = MagicMock()
    md.get_bars.return_value = bars
    broker = MagicMock()
    broker.get_account.return_value = BrokerAccount(
        equity=Decimal("100000"),
        cash=Decimal("100000"),
        buying_power=Decimal("200000"),
        paper=True,
    )
    broker.get_open_positions.return_value = []
    broker.has_open_position.return_value = False
    broker.submit_bracket_order.return_value = BrokerOrderResult(
        order_id="ord-99",
        symbol="AAPL",
        status="accepted",
        qty=Decimal("5"),
    )

    decision = SniperDecision(
        agent_id="large_cap_sniper",
        symbol="AAPL",
        status=SniperStatus.ENTER,
        trade_map=TradeMap(
            entry_trigger=Decimal("190"),
            scale_out_point=Decimal("193"),
            final_take_profit=Decimal("196"),
            stop_loss=Decimal("186"),
        ),
        reason="all_gates_passed",
    )

    with (
        patch("trading_lab.pipeline.paper_tick.resolve_market_data", return_value=md),
        patch("trading_lab.pipeline.paper_tick.AlpacaPaperBroker", return_value=broker),
        patch("trading_lab.pipeline.paper_tick.evaluate_large_cap_sniper", return_value=decision),
    ):
        out = run_paper_tick(symbol="AAPL", journal_path=str(tmp_path / "j.sqlite"))

    assert out["status"] == "ORDER_SUBMITTED"
    assert out["order_id"] == "ord-99"
    broker.submit_bracket_order.assert_called_once()

"""Multi-agent routing + swing power-hour submit gate."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import MagicMock, patch

from trading_lab.agents.sniper.decision import SniperDecision, TradeMap
from trading_lab.agents.sniper.shared_execution import SniperStatus
from trading_lab.agents.swing.decision import SwingDecision, SwingStatus, SwingTradeMap
from trading_lab.agents.swing.momentum import CapTier
from trading_lab.broker.types import BrokerAccount, BrokerOrderResult
from trading_lab.market_data.types import Bar
from trading_lab.pipeline.paper_agents import resolve_sniper_agent, run_symbol_paper_tick
from trading_lab.schemas.hold import HoldPlan, StrategyHorizon


def test_resolve_sniper_unknown_cap_is_speculative():
    assert resolve_sniper_agent(None, "ELVA") == "speculative_sniper"


def test_resolve_sniper_spy_is_large():
    assert resolve_sniper_agent(None, "SPY") == "large_cap_sniper"


def test_resolve_sniper_mid_cap_none():
    assert resolve_sniper_agent(Decimal("5000000000"), "XYZ") is None


def test_run_symbol_routes_speculative_and_defers_swing(tmp_path, monkeypatch):
    monkeypatch.setenv("USE_MOCK_BARS", "false")
    monkeypatch.setenv("ALPACA_API_KEY", "PK")
    monkeypatch.setenv("ALPACA_API_SECRET", "SK")

    bars = [
        Bar(
            symbol="ELVA",
            ts=datetime(2026, 7, 15, 14, i, tzinfo=UTC),
            open=Decimal("5"),
            high=Decimal("5.2"),
            low=Decimal("4.9"),
            close=Decimal("5"),
            volume=Decimal("10000"),
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
        order_id="ord-spec",
        symbol="ELVA",
        status="accepted",
        qty=Decimal("200"),
    )

    sniper_decision = SniperDecision(
        agent_id="speculative_sniper",
        symbol="ELVA",
        status=SniperStatus.ENTER,
        trade_map=TradeMap(
            entry_trigger=Decimal("5"),
            scale_out_point=Decimal("5.25"),
            final_take_profit=Decimal("5.5"),
            stop_loss=Decimal("4.8"),
        ),
    )
    swing_decision = SwingDecision(
        agent_id="swing_momentum",
        symbol="ELVA",
        status=SwingStatus.ENTER,
        cap_tier=CapTier.MICRO,
        trade_map=SwingTradeMap(
            entry_trigger=Decimal("5"),
            scale_out_point=Decimal("5.2"),
            final_take_profit=Decimal("5.6"),
            stop_loss=Decimal("4.75"),
        ),
        hold_plan=HoldPlan(
            horizon=StrategyHorizon.SWING,
            min_hold_sessions=1,
            typical_hold_sessions=3,
            max_hold_sessions=10,
            summary="swing",
        ),
    )

    with (
        patch("trading_lab.pipeline.paper_agents.resolve_market_data", return_value=md),
        patch("trading_lab.pipeline.paper_agents.AlpacaPaperBroker", return_value=broker),
        patch(
            "trading_lab.pipeline.paper_agents.evaluate_speculative_sniper",
            return_value=sniper_decision,
        ),
        patch("trading_lab.pipeline.paper_agents.swing_power_hour", return_value=False),
        patch("trading_lab.pipeline.swing_tick.resolve_market_data", return_value=md),
        patch("trading_lab.pipeline.swing_tick.AlpacaPaperBroker", return_value=broker),
        patch(
            "trading_lab.pipeline.swing_tick.evaluate_swing_momentum", return_value=swing_decision
        ),
        patch("trading_lab.pipeline.swing_tick.swing_power_hour", return_value=False),
    ):
        out = run_symbol_paper_tick(symbol="ELVA", journal_path=str(tmp_path / "j.sqlite"))

    assert out["sniper_agent"] == "speculative_sniper"
    assert out["sniper"]["status"] == "ORDER_SUBMITTED"
    assert out["swing"]["skips"] == 1
    assert (
        "power_hour" in (out["swing"].get("detail") or out["swing"].get("status", "")).lower()
        or out["swing"].get("detail") == "enter_deferred_until_power_hour"
    )
    # Only sniper submitted (swing deferred)
    assert broker.submit_bracket_order.call_count == 1

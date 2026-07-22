"""Swing momentum paper path — real daily bars; submit only in power hour."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

from trading_lab.agents.swing.decision import SwingStatus
from trading_lab.agents.swing.momentum import SWING_MOMENTUM
from trading_lab.broker.alpaca import AlpacaPaperBroker
from trading_lab.catalysts.congress import (
    MockUnusualWhalesCongress,
    UnusualWhalesCongress,
    congress_since,
)
from trading_lab.config.vendors import V1_VENDORS
from trading_lab.eval.swing import evaluate_swing_momentum
from trading_lab.journal.sqlite import SqliteJournal
from trading_lab.market_data.factory import resolve_market_data
from trading_lab.market_data.types import Bar, BarRequest, SessionContext
from trading_lab.pipeline.paper_submit import (
    make_risk_gate,
    qty_for_price,
    submit_paper_intent,
    write_skip,
)
from trading_lab.schedule import swing_power_hour
from trading_lab.schemas.trades import SkipReason

logger = logging.getLogger("trading_lab.swing_tick")


def _congress_port(*, use_mock: bool):
    if use_mock:
        return MockUnusualWhalesCongress()
    return UnusualWhalesCongress()


def _above_20dma(bars: list[Bar]) -> bool | None:
    if len(bars) < 20:
        return None
    closes = [b.close for b in bars[-20:]]
    ma = sum(closes, Decimal("0")) / Decimal(20)
    return bars[-1].close > ma


def evaluate_swing_with_congress(
    symbol: str,
    *,
    use_mock: bool = True,
    market_cap_usd: Decimal | None = None,
) -> dict:
    """Score-only helper (tests / legacy). Prefer run_swing_paper_tick for orders."""
    out = run_swing_paper_tick(
        symbol=symbol,
        journal_path="",  # unused when submit=False and no journal writes on score-only
        market_cap_usd=market_cap_usd,
        use_mock_bars=use_mock,
        submit=False,
        score_only=True,
    )
    return out


def run_swing_paper_tick(
    *,
    symbol: str,
    journal_path: str,
    market_cap_usd: Decimal | None = None,
    broker: AlpacaPaperBroker | None = None,
    notional_usd: Decimal | None = None,
    submit: bool | None = None,
    use_mock_bars: bool = False,
    score_only: bool = False,
) -> dict:
    """Evaluate swing_momentum on 1Day bars; paper bracket only when submit=True."""
    run_id = uuid4()
    end = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    start = end - timedelta(days=40)
    if use_mock_bars:
        from trading_lab.market_data.mock import MockMarketData

        md = MockMarketData()
    else:
        md = resolve_market_data()

    bars = md.get_bars(
        BarRequest(
            symbol=symbol,
            timeframe="1Day",
            start=start,
            end=end,
            feed=V1_VENDORS.alpaca_feed,
        )
    )
    if not bars:
        return {
            "symbol": symbol,
            "agent": "swing_momentum",
            "found_by_agent": "swing_momentum",
            "status": "NO_TRADE",
            "detail": "no_bars",
            "orders": 0,
            "skips": 0,
        }

    spy_bars = md.get_bars(
        BarRequest(
            symbol="SPY",
            timeframe="1Day",
            start=start,
            end=end,
            feed=V1_VENDORS.alpaca_feed,
        )
    )
    trend = _above_20dma(spy_bars)
    # Paper: missing SPY bars → do not invent a fail (same spirit as sniper catalyst).
    if trend is None and not use_mock_bars:
        trend = True

    port = _congress_port(use_mock=use_mock_bars)
    since = congress_since(SWING_MOMENTUM.congress_lookback_days)
    signals = (
        port.signals_for(symbol, since=since) if SWING_MOMENTUM.congress_catalyst_enabled else []
    )

    power = swing_power_hour() if submit is None else submit
    ctx = SessionContext(
        symbol=symbol,
        bar=bars[-1],
        bars=bars,
        market_cap_usd=market_cap_usd,
        # Let evaluator compute 8-EMA from bars (do not hardcode True).
        price_above_8ema=None,
        spy_or_qqq_above_20dma=trend,
        rs_vs_spy_qqq=None,
        in_power_hour=power,
        catalyst_signals=signals,
        has_catalyst=any(s.direction == "buy" for s in signals),
    )
    decision = evaluate_swing_momentum(ctx)
    base = {
        "symbol": symbol,
        "agent": decision.agent_id,
        "found_by_agent": decision.agent_id,
        "status": decision.status.value,
        "catalyst": decision.catalyst,
        "rvol": str(decision.rvol) if decision.rvol is not None else None,
        "priority": decision.meta.get("priority", 0),
        "congress_action": decision.meta.get("congress_action"),
        "reason": decision.reason,
        "source": SWING_MOMENTUM.congress_source,
        "congress_enabled": SWING_MOMENTUM.congress_catalyst_enabled,
        "orders": 0,
        "skips": 0,
    }

    if score_only or not journal_path:
        return base

    journal = SqliteJournal(journal_path)
    do_submit = power if submit is None else submit

    if decision.status != SwingStatus.ENTER or decision.trade_map is None:
        write_skip(
            journal,
            run_id=run_id,
            agent=decision.agent_id,
            symbol=symbol,
            ts=bars[-1].ts,
            skip_reason=SkipReason.SETUP_MISSING,
            detail=decision.reason or decision.status.value,
            meta={"swing_status": decision.status.value, "power_hour": do_submit},
        )
        base["skips"] = 1
        return base

    if not do_submit:
        write_skip(
            journal,
            run_id=run_id,
            agent=decision.agent_id,
            symbol=symbol,
            ts=bars[-1].ts,
            skip_reason=SkipReason.OUTSIDE_WINDOW,
            detail="swing_enter_outside_power_hour",
            meta={"swing_status": "ENTER", "power_hour": False},
        )
        base["status"] = "WATCH"
        base["detail"] = "enter_deferred_until_power_hour"
        base["skips"] = 1
        return base

    broker = broker or AlpacaPaperBroker()
    if broker.has_open_position(symbol):
        write_skip(
            journal,
            run_id=run_id,
            agent=decision.agent_id,
            symbol=symbol,
            ts=bars[-1].ts,
            skip_reason=SkipReason.MAX_POSITIONS,
            detail="already_open_on_alpaca_paper",
        )
        base["status"] = "SKIP"
        base["detail"] = "already_open"
        base["skips"] = 1
        return base

    risk, equity, use_notional = make_risk_gate(
        broker, journal_path=journal_path, notional_usd=notional_usd
    )
    qty = qty_for_price(decision.trade_map.entry_trigger, use_notional)
    if qty is None:
        write_skip(
            journal,
            run_id=run_id,
            agent=decision.agent_id,
            symbol=symbol,
            ts=bars[-1].ts,
            skip_reason=SkipReason.RISK_BLOCKED,
            detail=f"slice_cannot_buy_1_share price={decision.trade_map.entry_trigger}",
        )
        base["status"] = "RISK_BLOCKED"
        base["skips"] = 1
        return base
    intent = decision.to_trade_intent(qty)
    assert intent is not None
    submitted = submit_paper_intent(
        broker=broker,
        journal=journal,
        risk=risk,
        intent=intent,
        run_id=run_id,
        bar_ts=bars[-1].ts,
        equity=equity,
        notional_usd=use_notional,
        journal_path=journal_path,
    )
    return {**base, **submitted}

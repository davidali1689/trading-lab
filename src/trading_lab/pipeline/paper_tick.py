"""Live paper tick: Alpaca IEX bars → sniper eval → Alpaca paper bracket order.

Uses the paper account ($100k sim). Never forces a trade when setup is missing.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from decimal import ROUND_DOWN, Decimal
from uuid import uuid4

from trading_lab.agents.sniper.shared_execution import SniperStatus
from trading_lab.broker.alpaca import AlpacaPaperBroker
from trading_lab.config.vendors import V1_VENDORS
from trading_lab.eval.large_cap import evaluate_large_cap_sniper
from trading_lab.execution.risk_gate import RiskGate, RiskGateConfig
from trading_lab.journal.sqlite import SqliteJournal
from trading_lab.market_data.factory import resolve_market_data
from trading_lab.market_data.types import BarRequest, SessionContext
from trading_lab.schemas.trades import (
    ExitReason,
    RunMode,
    Side,
    SkipEvent,
    SkipReason,
    TradeRecord,
)

logger = logging.getLogger("trading_lab.paper_tick")

# ~1% of $100k paper equity per new position (sniper risk budget)
DEFAULT_NOTIONAL_USD = Decimal("1000")


def _qty_for_price(entry: Decimal, notional: Decimal = DEFAULT_NOTIONAL_USD) -> Decimal:
    if entry <= 0:
        return Decimal("1")
    qty = (notional / entry).to_integral_value(rounding=ROUND_DOWN)
    return qty if qty >= 1 else Decimal("1")


def run_paper_tick(
    *,
    symbol: str,
    journal_path: str,
    market_cap_usd: Decimal = Decimal("3000000000000"),
    notional_usd: Decimal = DEFAULT_NOTIONAL_USD,
) -> dict:
    """Evaluate latest bar and submit Alpaca paper order if ENTER + risk allows."""
    run_id = uuid4()
    end = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    start = end - timedelta(hours=2)
    md = resolve_market_data()
    bars = md.get_bars(
        BarRequest(
            symbol=symbol,
            timeframe="1Min",
            start=start,
            end=end,
            feed=V1_VENDORS.alpaca_feed,
        )
    )
    journal = SqliteJournal(journal_path)
    if len(bars) < 21:
        return {
            "symbol": symbol,
            "mode": "paper",
            "status": "NO_TRADE",
            "detail": f"insufficient_bars={len(bars)}",
            "orders": 0,
            "skips": 0,
        }

    broker = AlpacaPaperBroker()
    account = broker.get_account()
    positions = broker.get_open_positions()

    risk = RiskGate(
        config=RiskGateConfig(
            starting_capital=account.equity or Decimal("100000"),
            max_position_notional_usd=max(notional_usd, Decimal("10000")),
        )
    )
    risk.state.open_positions = len(positions)

    if broker.has_open_position(symbol):
        skip = SkipEvent(
            event_id=uuid4(),
            run_id=run_id,
            found_by_agent="large_cap_sniper",
            symbol=symbol,
            ts=bars[-1].ts,
            mode=RunMode.PAPER,
            skip_reason=SkipReason.MAX_POSITIONS,
            detail="already_open_on_alpaca_paper",
            bar_ts=bars[-1].ts,
            meta={"equity": str(account.equity)},
        )
        journal.write_skip(skip)
        return {
            "symbol": symbol,
            "mode": "paper",
            "status": "SKIP",
            "detail": "already_open",
            "equity": str(account.equity),
            "orders": 0,
            "skips": 1,
        }

    bar = bars[-1]
    ctx = SessionContext(
        symbol=symbol,
        bar=bar,
        bars=bars,
        market_cap_usd=market_cap_usd,
        has_catalyst=False,
        spy_aligned=True,
        qqq_aligned=True,
    )
    decision = evaluate_large_cap_sniper(ctx, mode=RunMode.PAPER)

    if decision.status != SniperStatus.ENTER or decision.trade_map is None:
        reason = (
            SkipReason.SETUP_MISSING
            if decision.status == SniperStatus.NO_TRADE
            else SkipReason.OUTSIDE_WINDOW
        )
        skip = SkipEvent(
            event_id=uuid4(),
            run_id=run_id,
            found_by_agent=decision.agent_id,
            symbol=symbol,
            ts=bar.ts,
            mode=RunMode.PAPER,
            skip_reason=reason,
            detail=decision.reason or decision.status.value,
            bar_ts=bar.ts,
            meta={"sniper_status": decision.status.value},
        )
        journal.write_skip(skip)
        return {
            "symbol": symbol,
            "mode": "paper",
            "status": decision.status.value,
            "detail": decision.reason,
            "equity": str(account.equity),
            "orders": 0,
            "skips": 1,
        }

    qty = _qty_for_price(decision.trade_map.entry_trigger, notional_usd)
    intent = decision.to_trade_intent(qty)
    assert intent is not None
    gate = risk.check(intent, bar.ts)
    if not gate.allowed:
        skip = SkipEvent(
            event_id=uuid4(),
            run_id=run_id,
            found_by_agent=decision.agent_id,
            symbol=symbol,
            ts=bar.ts,
            mode=RunMode.PAPER,
            skip_reason=gate.skip_reason or SkipReason.RISK_BLOCKED,
            detail=gate.detail,
            bar_ts=bar.ts,
        )
        journal.write_skip(skip)
        return {
            "symbol": symbol,
            "mode": "paper",
            "status": "RISK_BLOCKED",
            "detail": gate.detail,
            "equity": str(account.equity),
            "orders": 0,
            "skips": 1,
        }

    order = broker.submit_bracket_order(intent)
    risk.on_open()
    # Provisional ledger row until bracket closes (exit=entry, open=true).
    trade = TradeRecord(
        trade_id=uuid4(),
        run_id=run_id,
        found_by_agent=decision.agent_id,
        symbol=symbol,
        side=Side.LONG,
        mode=RunMode.PAPER,
        setup_tags=intent.setup_tags,
        entry_ts=bar.ts,
        entry_px=intent.entry_px,
        qty=intent.qty,
        stop_px=intent.stop_px,
        target_px=intent.target_px,
        hold_plan=intent.hold_plan,
        exit_ts=bar.ts,
        exit_px=intent.entry_px,
        exit_reason=ExitReason.MANUAL,
        bars_held=0,
        fill_model="alpaca_paper_bracket",
        meta={
            "open": True,
            "alpaca_order_id": order.order_id,
            "alpaca_status": order.status,
            "equity": str(account.equity),
            "paper_account": True,
        },
    )
    journal.write_trade(trade)
    logger.info(
        "paper order submitted symbol=%s order_id=%s qty=%s equity=%s",
        symbol,
        order.order_id,
        qty,
        account.equity,
    )
    return {
        "symbol": symbol,
        "mode": "paper",
        "status": "ORDER_SUBMITTED",
        "order_id": order.order_id,
        "qty": str(qty),
        "equity": str(account.equity),
        "orders": 1,
        "skips": 0,
    }

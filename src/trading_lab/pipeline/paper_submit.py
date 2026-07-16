"""Shared Alpaca paper submit + journal helpers for all agents."""

from __future__ import annotations

import logging
from datetime import datetime
from decimal import ROUND_DOWN, Decimal
from uuid import UUID, uuid4

from trading_lab.broker.alpaca import AlpacaPaperBroker
from trading_lab.execution.budget import risk_config_from_equity, slice_notional
from trading_lab.execution.risk_gate import RiskGate
from trading_lab.journal.sqlite import SqliteJournal
from trading_lab.schemas.trades import (
    ExitReason,
    RunMode,
    Side,
    SkipEvent,
    SkipReason,
    TradeIntent,
    TradeRecord,
)

logger = logging.getLogger("trading_lab.paper_submit")

# Legacy default for explicit test overrides only — live/paper use equity/5.
DEFAULT_NOTIONAL_USD = Decimal("1000")


def qty_for_price(entry: Decimal, notional: Decimal = DEFAULT_NOTIONAL_USD) -> Decimal:
    if entry <= 0:
        return Decimal("1")
    qty = (notional / entry).to_integral_value(rounding=ROUND_DOWN)
    return qty if qty >= 1 else Decimal("1")


def make_risk_gate(
    broker: AlpacaPaperBroker,
    *,
    notional_usd: Decimal | None = None,
) -> tuple[RiskGate, Decimal, Decimal]:
    """Return (risk_gate, equity, per_agent_notional).

    Equity is always read from the trading platform (Alpaca paper/live).
    Per-agent notional = current equity/5. Max 3 open positions.
    Recalculated every tick so compounded gains/losses resize the book.
    """
    account = broker.get_account()
    equity = account.equity
    if equity is None or equity <= 0:
        raise RuntimeError("platform equity unavailable or non-positive — refuse to size trades")
    one_slice = slice_notional(equity)
    use_notional = notional_usd if notional_usd is not None else one_slice
    # Never size above one slice of *current* equity.
    use_notional = min(use_notional, one_slice)
    risk = RiskGate(config=risk_config_from_equity(equity))
    risk.state.open_positions = len(broker.get_open_positions())
    logger.info(
        "budget equity=%s slice=%s max_open=%s",
        equity,
        one_slice,
        risk.config.max_open_positions,
    )
    return risk, equity, use_notional


def write_skip(
    journal: SqliteJournal,
    *,
    run_id: UUID,
    agent: str,
    symbol: str,
    ts: datetime,
    skip_reason: SkipReason,
    detail: str,
    meta: dict | None = None,
) -> None:
    journal.write_skip(
        SkipEvent(
            event_id=uuid4(),
            run_id=run_id,
            found_by_agent=agent,
            symbol=symbol,
            ts=ts,
            mode=RunMode.PAPER,
            skip_reason=skip_reason,
            detail=detail,
            bar_ts=ts,
            meta=meta or {},
        )
    )


def submit_paper_intent(
    *,
    broker: AlpacaPaperBroker,
    journal: SqliteJournal,
    risk: RiskGate,
    intent: TradeIntent,
    run_id: UUID,
    bar_ts: datetime,
    equity: Decimal,
    notional_usd: Decimal | None = None,
) -> dict:
    """Risk-check + bracket submit + provisional journal trade. ENTER must not silently drop."""
    agent = intent.found_by_agent
    symbol = intent.symbol
    gate = risk.check(intent, bar_ts)
    if not gate.allowed:
        write_skip(
            journal,
            run_id=run_id,
            agent=agent,
            symbol=symbol,
            ts=bar_ts,
            skip_reason=gate.skip_reason or SkipReason.RISK_BLOCKED,
            detail=gate.detail,
        )
        return {
            "symbol": symbol,
            "mode": "paper",
            "status": "RISK_BLOCKED",
            "found_by_agent": agent,
            "detail": gate.detail,
            "equity": str(equity),
            "orders": 0,
            "skips": 1,
        }

    logger.info(
        "ENTER %s agent=%s qty=%s entry=%s — submitting Alpaca paper bracket",
        symbol,
        agent,
        intent.qty,
        intent.entry_px,
    )
    order = broker.submit_bracket_order(intent)
    risk.on_open()
    trade = TradeRecord(
        trade_id=uuid4(),
        run_id=run_id,
        found_by_agent=agent,
        symbol=symbol,
        side=Side.LONG,
        mode=RunMode.PAPER,
        setup_tags=intent.setup_tags,
        entry_ts=bar_ts,
        entry_px=intent.entry_px,
        qty=intent.qty,
        stop_px=intent.stop_px,
        target_px=intent.target_px,
        hold_plan=intent.hold_plan,
        exit_ts=bar_ts,
        exit_px=intent.entry_px,
        exit_reason=ExitReason.MANUAL,
        bars_held=0,
        fill_model="alpaca_paper_bracket",
        meta={
            "open": True,
            "alpaca_order_id": order.order_id,
            "alpaca_status": order.status,
            "equity": str(equity),
            "paper_account": True,
            "notional_usd": str(notional_usd) if notional_usd is not None else "",
        },
    )
    journal.write_trade(trade)
    return {
        "symbol": symbol,
        "mode": "paper",
        "status": "ORDER_SUBMITTED",
        "found_by_agent": agent,
        "order_id": order.order_id,
        "qty": str(intent.qty),
        "equity": str(equity),
        "orders": 1,
        "skips": 0,
    }

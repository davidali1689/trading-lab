"""Shared Alpaca paper submit + journal helpers for all agents."""

from __future__ import annotations

import logging
from datetime import datetime
from decimal import ROUND_DOWN, Decimal
from uuid import UUID, uuid4

from trading_lab.broker.alpaca import AlpacaPaperBroker
from trading_lab.execution.budget import risk_config_from_equity, slice_notional
from trading_lab.execution.risk_gate import RiskGate
from trading_lab.execution.risk_persist import load_risk_gate, save_risk_gate
from trading_lab.journal.sqlite import SqliteJournal
from trading_lab.schemas.hold import StrategyHorizon
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


def qty_for_price(entry: Decimal, notional: Decimal = DEFAULT_NOTIONAL_USD) -> Decimal | None:
    """Shares affordable at notional. None if cannot buy ≥1 share within slice."""
    if entry <= 0:
        return None
    qty = (notional / entry).to_integral_value(rounding=ROUND_DOWN)
    if qty < 1:
        return None
    return qty


def make_risk_gate(
    broker: AlpacaPaperBroker,
    *,
    journal_path: str | None = None,
    notional_usd: Decimal | None = None,
) -> tuple[RiskGate, Decimal, Decimal]:
    """Return (risk_gate, equity, per_agent_notional).

    Equity is always read from the trading platform (Alpaca paper/live).
    Per-agent notional = current equity/5. Max 3 open positions.
    When journal_path is set, daily-loss / cool-off state persists across ticks.
    """
    account = broker.get_account()
    equity = account.equity
    if equity is None or equity <= 0:
        raise RuntimeError("platform equity unavailable or non-positive — refuse to size trades")
    one_slice = slice_notional(equity)
    use_notional = notional_usd if notional_usd is not None else one_slice
    use_notional = min(use_notional, one_slice)
    cfg = risk_config_from_equity(equity)
    if journal_path:
        risk = load_risk_gate(journal_path, config=cfg)
        risk.config = cfg
    else:
        risk = RiskGate(config=cfg)
    positions = broker.get_open_positions()
    risk.state.open_positions = len(positions)
    risk.state.open_unrealized_pl = sum(
        (p.unrealized_pl for p in positions), Decimal("0")
    )
    logger.info(
        "budget equity=%s slice=%s max_open=%s cool_until=%s day_pnl=%s",
        equity,
        one_slice,
        risk.config.max_open_positions,
        risk.state.cooling_off_until,
        risk.state.realized_pnl_today,
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


def _bracket_legs(broker: AlpacaPaperBroker, order_id: str) -> list[dict] | None:
    """Nested bracket legs, or None when the broker does not expose them."""
    try:
        raw = broker.get_order(order_id)
    except Exception:  # noqa: BLE001 — cannot verify; do not punish the entry
        return None
    legs = raw.get("legs")
    if not isinstance(legs, list) or not legs:
        return None
    return [leg for leg in legs if isinstance(leg, dict)]


def _has_stop_leg(legs: list[dict]) -> bool:
    for leg in legs:
        side = str(leg.get("side") or "").lower()
        otype = str(leg.get("type") or "").lower()
        if side == "sell" and otype.startswith("stop"):
            return True
    return False


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
    journal_path: str | None = None,
    wait_fill: bool = True,
) -> dict:
    """Risk-check + bracket submit + journal only after fill confirmation."""
    agent = intent.found_by_agent
    symbol = intent.symbol

    if intent.side != Side.LONG:
        write_skip(
            journal,
            run_id=run_id,
            agent=agent,
            symbol=symbol,
            ts=bar_ts,
            skip_reason=SkipReason.RISK_BLOCKED,
            detail="long_only_broker_v0",
        )
        return {
            "symbol": symbol,
            "mode": "paper",
            "status": "RISK_BLOCKED",
            "found_by_agent": agent,
            "detail": "long_only_broker_v0",
            "equity": str(equity),
            "orders": 0,
            "skips": 1,
        }

    # Gap 6: swing settled-funds preference
    if (
        intent.hold_plan.horizon == StrategyHorizon.SWING and intent.hold_plan  # noqa: SIM201
    ):
        acct = broker.get_account()
        settled = acct.settled_cash if acct.settled_cash is not None else acct.cash
        need = intent.entry_px * intent.qty
        if settled is not None and settled < need:
            write_skip(
                journal,
                run_id=run_id,
                agent=agent,
                symbol=symbol,
                ts=bar_ts,
                skip_reason=SkipReason.UNSETTLED_FUNDS,
                detail=f"settled_cash={settled} need={need}",
            )
            return {
                "symbol": symbol,
                "mode": "paper",
                "status": "RISK_BLOCKED",
                "found_by_agent": agent,
                "detail": "unsettled_funds",
                "equity": str(equity),
                "orders": 0,
                "skips": 1,
            }

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
    fill = order
    if wait_fill and isinstance(broker, AlpacaPaperBroker) and order.order_id:
        fill = broker.wait_for_fill(order.order_id)
    status = str(fill.status or order.status or "").lower()
    if status in {"canceled", "expired", "rejected", "replaced"}:
        write_skip(
            journal,
            run_id=run_id,
            agent=agent,
            symbol=symbol,
            ts=bar_ts,
            skip_reason=SkipReason.NO_LIQUIDITY,
            detail=f"order_not_filled status={fill.status}",
            meta={"alpaca_order_id": order.order_id, "alpaca_status": fill.status},
        )
        return {
            "symbol": symbol,
            "mode": "paper",
            "status": "ORDER_REJECTED",
            "found_by_agent": agent,
            "order_id": order.order_id,
            "detail": f"order_not_filled status={fill.status}",
            "equity": str(equity),
            "orders": 0,
            "skips": 1,
        }

    entry_px = fill.filled_avg_price or intent.entry_px
    qty = fill.qty if fill.qty > 0 else intent.qty

    # F3: never sit naked — if Alpaca exposes legs and the stop leg is absent,
    # flatten immediately instead of riding an unprotected position (ZYBT 07-21).
    if (
        isinstance(broker, AlpacaPaperBroker)
        and order.order_id
        and status in {"filled", "partially_filled"}
    ):
        legs = _bracket_legs(broker, order.order_id)
        if legs is not None and not _has_stop_leg(legs):
            logger.error(
                "bracket stop leg missing for %s order=%s — fail-safe flatten",
                symbol,
                order.order_id,
            )
            try:
                broker.cancel_open_orders(symbol)
                broker.close_position(symbol)
            except Exception:  # noqa: BLE001
                logger.exception("fail-safe flatten failed for %s", symbol)
            trade = TradeRecord(
                trade_id=uuid4(),
                run_id=run_id,
                found_by_agent=agent,
                symbol=symbol,
                side=Side.LONG,
                mode=RunMode.PAPER,
                setup_tags=intent.setup_tags,
                entry_ts=bar_ts,
                entry_px=entry_px,
                qty=qty,
                stop_px=intent.stop_px,
                target_px=intent.target_px,
                hold_plan=intent.hold_plan,
                exit_ts=bar_ts,
                exit_px=entry_px,
                exit_reason=ExitReason.RISK_KILL,
                bars_held=0,
                fill_model="alpaca_paper_bracket",
                meta={
                    "open": False,
                    "alpaca_order_id": order.order_id,
                    "alpaca_status": fill.status,
                    "equity": str(equity),
                    "paper_account": True,
                    "stop_leg_missing": True,
                    "closed_by": "bracket_leg_failsafe",
                },
            )
            journal.write_trade(trade)
            return {
                "symbol": symbol,
                "mode": "paper",
                "status": "STOP_LEG_MISSING_FLATTENED",
                "found_by_agent": agent,
                "order_id": order.order_id,
                "equity": str(equity),
                "orders": 1,
                "skips": 0,
            }

    risk.on_open()
    if journal_path:
        save_risk_gate(journal_path, risk)

    scale_pt = (intent.meta or {}).get("scale_out_point")
    trade = TradeRecord(
        trade_id=uuid4(),
        run_id=run_id,
        found_by_agent=agent,
        symbol=symbol,
        side=Side.LONG,
        mode=RunMode.PAPER,
        setup_tags=intent.setup_tags,
        entry_ts=bar_ts,
        entry_px=entry_px,
        qty=qty,
        stop_px=intent.stop_px,
        target_px=intent.target_px,
        hold_plan=intent.hold_plan,
        exit_ts=bar_ts,
        exit_px=entry_px,
        exit_reason=ExitReason.MANUAL,
        bars_held=0,
        fill_model="alpaca_paper_bracket",
        meta={
            "open": True,
            "alpaca_order_id": order.order_id,
            "alpaca_status": fill.status,
            "equity": str(equity),
            "paper_account": True,
            "notional_usd": str(notional_usd) if notional_usd is not None else "",
            "scale_out_point": str(scale_pt) if scale_pt else "",
            "fill_confirmed": status in {"filled", "partially_filled"},
        },
    )
    journal.write_trade(trade)
    return {
        "symbol": symbol,
        "mode": "paper",
        "status": "ORDER_SUBMITTED",
        "found_by_agent": agent,
        "order_id": order.order_id,
        "qty": str(qty),
        "equity": str(equity),
        "orders": 1,
        "skips": 0,
    }

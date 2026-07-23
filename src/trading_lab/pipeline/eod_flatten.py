"""EOD flatten — close intraday sniper paper positions; leave swing overnight."""

from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any

from trading_lab.broker.alpaca import AlpacaPaperBroker
from trading_lab.config.secrets import has_alpaca_keys
from trading_lab.journal.open_trades import close_journal_trade, load_open_plans
from trading_lab.pipeline.exit_reassess import SNIPER_AGENTS
from trading_lab.schemas.trades import ExitReason

logger = logging.getLogger("trading_lab.eod_flatten")


def flatten_sniper_paper(journal_path: str) -> list[dict[str, Any]]:
    """Close Alpaca paper positions from open sniper journal rows only."""
    if not has_alpaca_keys():
        return [{"ok": False, "detail": "no_alpaca_keys"}]

    plans = load_open_plans(journal_path)
    symbols = {sym for sym, plan in plans.items() if plan.get("found_by_agent") in SNIPER_AGENTS}

    if not symbols:
        return [{"ok": True, "detail": "no_open_sniper_positions"}]

    broker = AlpacaPaperBroker()
    marks = {p.symbol.upper(): p.current_price for p in broker.get_open_positions()}
    out: list[dict[str, Any]] = []
    for sym in sorted(symbols):
        try:
            broker.cancel_open_orders(sym)
            broker.close_position(sym)
            plan = plans.get(sym) or {}
            mark = marks.get(sym) or plan.get("entry_px") or Decimal("0")
            close_journal_trade(
                journal_path,
                sym,
                exit_px=Decimal(str(mark)),
                exit_reason=ExitReason.EOD,
                closed_by="eod_flatten",
                trade_id=plan.get("trade_id"),
            )
            out.append({"symbol": sym, "ok": True, "exit_px": str(mark)})
            logger.info("EOD flattened sniper position %s", sym)
        except Exception as exc:  # noqa: BLE001
            out.append({"symbol": sym, "ok": False, "detail": str(exc)})
            logger.exception("EOD flatten failed for %s", sym)
    return out

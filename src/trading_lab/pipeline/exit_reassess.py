"""Daily exit reassessment for open paper positions.

Day brackets expire overnight; swing holds must never sit naked.
Premarket + tick call `reassess_open_exits` to flatten, scale, or re-arm GTC OCO.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from decimal import ROUND_DOWN, Decimal
from enum import StrEnum
from pathlib import Path
from typing import Any

from trading_lab.agents.swing.shared_execution import SWING_SHARED
from trading_lab.broker.alpaca import AlpacaPaperBroker
from trading_lab.config.secrets import has_alpaca_keys

logger = logging.getLogger("trading_lab.exit_reassess")


class ExitAction(StrEnum):
    FLATTEN_TARGET = "flatten_target"
    FLATTEN_STOP = "flatten_stop"
    SCALE_AND_TRAIL = "scale_and_trail"
    REARM_OCO = "rearm_oco"
    NOOP_HAS_EXITS = "noop_has_exits"
    SKIP_NO_PLAN = "skip_no_plan"


def assess_exit_action(
    *,
    entry: Decimal,
    mark: Decimal,
    stop: Decimal,
    target: Decimal,
    scale_gain_pct: Decimal = SWING_SHARED.scale_out_gain_pct,
) -> ExitAction:
    """Pure ladder: stop → rearm → scale → harvest past final target."""
    if mark <= stop:
        return ExitAction.FLATTEN_STOP
    if mark >= target:
        return ExitAction.FLATTEN_TARGET
    scale_px = entry * (Decimal("1") + scale_gain_pct / Decimal("100"))
    if mark >= scale_px:
        return ExitAction.SCALE_AND_TRAIL
    return ExitAction.REARM_OCO


def _load_open_plans(journal_path: str) -> dict[str, dict[str, Any]]:
    """Latest open journal row per symbol → entry/stop/target/qty/meta."""
    path = Path(journal_path)
    if not path.exists():
        return {}
    plans: dict[str, dict[str, Any]] = {}
    with sqlite3.connect(path) as conn:
        for row in conn.execute(
            "SELECT symbol, found_by_agent, entry_px, qty, payload FROM trades ORDER BY entry_ts ASC"
        ):
            symbol, agent, entry_px, qty, payload_raw = row
            try:
                payload = json.loads(payload_raw or "{}")
            except json.JSONDecodeError:
                payload = {}
            meta = payload.get("meta") or {}
            if meta.get("open") is not True:
                continue
            sym = str(symbol).upper()
            stop_raw = payload.get("stop_px")
            target_raw = payload.get("target_px")
            plans[sym] = {
                "symbol": sym,
                "found_by_agent": agent,
                "entry_px": Decimal(str(entry_px)),
                "stop_px": Decimal(str(stop_raw or "0")),
                "target_px": Decimal(str(target_raw or "0")),
                "qty": Decimal(str(qty)),
                "meta": meta,
                "payload": payload,
            }
    return plans


def _half_qty(qty: Decimal) -> Decimal:
    half = (qty * Decimal("0.5")).to_integral_value(rounding=ROUND_DOWN)
    return half if half >= 1 else Decimal("1")


def _has_sell_exit(orders: list[dict[str, Any]], symbol: str) -> bool:
    sym = symbol.upper()
    for o in orders:
        if str(o.get("symbol") or "").upper() != sym:
            continue
        if str(o.get("side") or "").lower() != "sell":
            continue
        status = str(o.get("status") or "").lower()
        if status in {"new", "accepted", "pending_new", "held", "partially_filled"}:
            return True
    return False


def _mark_journal_closed(journal_path: str, symbol: str) -> None:
    path = Path(journal_path)
    if not path.exists():
        return
    sym = symbol.upper()
    with sqlite3.connect(path) as conn:
        rows = list(
            conn.execute("SELECT trade_id, payload FROM trades WHERE upper(symbol)=?", (sym,))
        )
        for trade_id, payload_raw in rows:
            try:
                payload = json.loads(payload_raw or "{}")
            except json.JSONDecodeError:
                continue
            meta = payload.get("meta") or {}
            if meta.get("open") is not True:
                continue
            meta["open"] = False
            meta["closed_by"] = "exit_reassess"
            payload["meta"] = meta
            conn.execute(
                "UPDATE trades SET payload=? WHERE trade_id=?",
                (json.dumps(payload), trade_id),
            )


def _mark_journal_scaled(journal_path: str, symbol: str, remain_qty: Decimal) -> None:
    path = Path(journal_path)
    if not path.exists():
        return
    sym = symbol.upper()
    with sqlite3.connect(path) as conn:
        rows = list(
            conn.execute("SELECT trade_id, payload FROM trades WHERE upper(symbol)=?", (sym,))
        )
        for trade_id, payload_raw in rows:
            try:
                payload = json.loads(payload_raw or "{}")
            except json.JSONDecodeError:
                continue
            meta = payload.get("meta") or {}
            if meta.get("open") is not True:
                continue
            meta["scaled_out"] = True
            meta["remain_qty"] = str(remain_qty)
            payload["meta"] = meta
            conn.execute(
                "UPDATE trades SET payload=? WHERE trade_id=?",
                (json.dumps(payload), trade_id),
            )


def reassess_open_exits(
    journal_path: str,
    *,
    broker: AlpacaPaperBroker | None = None,
) -> list[dict[str, Any]]:
    """Assess every open broker position and enforce an exit plan."""
    if broker is None:
        if not has_alpaca_keys():
            return [{"ok": False, "detail": "no_alpaca_keys"}]
        broker = AlpacaPaperBroker()

    plans = _load_open_plans(journal_path)
    positions = broker.get_open_positions()
    if not positions:
        return [{"ok": True, "detail": "no_open_positions"}]

    open_orders = broker.list_open_orders()
    out: list[dict[str, Any]] = []

    for pos in positions:
        sym = pos.symbol.upper()
        if pos.qty <= 0:
            continue
        plan = plans.get(sym)
        mark = pos.current_price if pos.current_price > 0 else pos.avg_entry_price
        entry = pos.avg_entry_price if pos.avg_entry_price > 0 else (
            plan["entry_px"] if plan else Decimal("0")
        )
        if plan is None or entry <= 0 or plan["stop_px"] <= 0 or plan["target_px"] <= 0:
            out.append(
                {
                    "ok": False,
                    "symbol": sym,
                    "action": ExitAction.SKIP_NO_PLAN.value,
                    "detail": "open_on_broker_without_journal_exit_plan",
                }
            )
            continue

        stop = plan["stop_px"]
        target = plan["target_px"]
        action = assess_exit_action(entry=entry, mark=mark, stop=stop, target=target)
        has_exits = _has_sell_exit(open_orders, sym)
        scaled = bool((plan.get("meta") or {}).get("scaled_out"))

        try:
            if action in {ExitAction.FLATTEN_TARGET, ExitAction.FLATTEN_STOP}:
                if has_exits:
                    broker.cancel_open_orders(sym)
                broker.close_position(sym)
                _mark_journal_closed(journal_path, sym)
                out.append(
                    {
                        "ok": True,
                        "symbol": sym,
                        "action": action.value,
                        "mark": str(mark),
                        "target": str(target),
                        "stop": str(stop),
                    }
                )
                continue

            if action == ExitAction.SCALE_AND_TRAIL:
                remain = pos.qty
                if not scaled and pos.qty >= 2:
                    sell_qty = _half_qty(pos.qty)
                    broker.cancel_open_orders(sym)
                    broker.close_position(sym, qty=sell_qty)
                    remain = pos.qty - sell_qty
                    if remain < 1:
                        remain = Decimal("1")
                    _mark_journal_scaled(journal_path, sym, remain)
                    has_exits = False
                be_stop = entry  # move stop to breakeven on remainder
                if not has_exits:
                    broker.submit_oco_exit(
                        symbol=sym,
                        qty=remain if scaled else remain,
                        stop_px=be_stop,
                        target_px=target,
                        time_in_force="gtc",
                    )
                out.append(
                    {
                        "ok": True,
                        "symbol": sym,
                        "action": action.value,
                        "mark": str(mark),
                        "remain_qty": str(remain),
                        "stop": str(be_stop),
                        "target": str(target),
                        "scaled": True,
                    }
                )
                continue

            # REARM_OCO
            if has_exits:
                out.append(
                    {
                        "ok": True,
                        "symbol": sym,
                        "action": ExitAction.NOOP_HAS_EXITS.value,
                        "mark": str(mark),
                    }
                )
                continue
            broker.submit_oco_exit(
                symbol=sym,
                qty=pos.qty,
                stop_px=stop,
                target_px=target,
                time_in_force="gtc",
            )
            out.append(
                {
                    "ok": True,
                    "symbol": sym,
                    "action": ExitAction.REARM_OCO.value,
                    "mark": str(mark),
                    "stop": str(stop),
                    "target": str(target),
                    "qty": str(pos.qty),
                }
            )
        except Exception as exc:  # noqa: BLE001 — continue other symbols
            logger.exception("exit reassess failed for %s", sym)
            out.append({"ok": False, "symbol": sym, "action": action.value, "detail": str(exc)})

    return out

"""Journal helpers for open paper trades — close with real P&L, never leave ghosts."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from trading_lab.schemas.trades import ExitReason


def load_open_plans(journal_path: str | Path) -> dict[str, dict[str, Any]]:
    """Latest open journal row per symbol."""
    path = Path(journal_path)
    if not path.exists():
        return {}
    plans: dict[str, dict[str, Any]] = {}
    with sqlite3.connect(path) as conn:
        for row in conn.execute(
            "SELECT trade_id, symbol, found_by_agent, entry_ts, entry_px, qty, payload "
            "FROM trades ORDER BY entry_ts ASC"
        ):
            trade_id, symbol, agent, entry_ts, entry_px, qty, payload_raw = row
            try:
                payload = json.loads(payload_raw or "{}")
            except json.JSONDecodeError:
                payload = {}
            meta = payload.get("meta") or {}
            if meta.get("open") is not True:
                continue
            sym = str(symbol).upper()
            plans[sym] = {
                "trade_id": trade_id,
                "symbol": sym,
                "found_by_agent": str(agent),
                "entry_ts": entry_ts,
                "entry_px": Decimal(str(entry_px)),
                "stop_px": Decimal(str(payload.get("stop_px") or "0")),
                "target_px": Decimal(str(payload.get("target_px") or "0")),
                "scale_out_point": (
                    Decimal(str(payload.get("meta", {}).get("scale_out_point")))
                    if (payload.get("meta") or {}).get("scale_out_point")
                    else None
                ),
                "qty": Decimal(str(qty)),
                "meta": meta,
                "payload": payload,
                "hold_plan": payload.get("hold_plan") or {},
            }
    return plans


def close_journal_trade(
    journal_path: str | Path,
    symbol: str,
    *,
    exit_px: Decimal,
    exit_reason: ExitReason,
    exit_ts: datetime | None = None,
    closed_by: str = "reconcile",
) -> bool:
    """Mark open journal row closed and write real exit/P&L columns."""
    path = Path(journal_path)
    if not path.exists():
        return False
    sym = symbol.upper()
    ts = exit_ts or datetime.now(timezone.utc)
    closed = False
    with sqlite3.connect(path) as conn:
        rows = list(
            conn.execute(
                "SELECT trade_id, entry_px, qty, payload FROM trades WHERE upper(symbol)=?",
                (sym,),
            )
        )
        for trade_id, entry_px, qty, payload_raw in rows:
            try:
                payload = json.loads(payload_raw or "{}")
            except json.JSONDecodeError:
                continue
            meta = payload.get("meta") or {}
            if meta.get("open") is not True:
                continue
            entry = Decimal(str(entry_px))
            q = Decimal(str(qty))
            pnl_usd = (exit_px - entry) * q
            pnl_pct = (pnl_usd / (entry * q) * Decimal("100")) if entry * q != 0 else Decimal("0")
            meta["open"] = False
            meta["closed_by"] = closed_by
            payload["meta"] = meta
            payload["exit_px"] = str(exit_px)
            payload["exit_ts"] = ts.isoformat()
            payload["exit_reason"] = exit_reason.value
            payload["pnl_usd"] = str(pnl_usd)
            payload["pnl_pct"] = str(pnl_pct)
            conn.execute(
                """
                UPDATE trades SET
                  exit_ts=?, exit_px=?, exit_reason=?, pnl_usd=?, pnl_pct=?, payload=?
                WHERE trade_id=?
                """,
                (
                    ts.isoformat(),
                    str(exit_px),
                    exit_reason.value,
                    str(pnl_usd),
                    str(pnl_pct),
                    json.dumps(payload),
                    trade_id,
                ),
            )
            closed = True
    return closed


def mark_journal_scaled(
    journal_path: str | Path,
    symbol: str,
    remain_qty: Decimal,
    *,
    trail_stop: Decimal | None = None,
) -> None:
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
            if trail_stop is not None:
                meta["trail_stop"] = str(trail_stop)
            payload["meta"] = meta
            conn.execute(
                "UPDATE trades SET payload=? WHERE trade_id=?",
                (json.dumps(payload), trade_id),
            )


def update_last_mark(journal_path: str | Path, symbol: str, mark: Decimal) -> None:
    """Stamp last seen mark so reconcile can book real-ish P&L if fill price missing."""
    path = Path(journal_path)
    if not path.exists() or mark <= 0:
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
            meta["last_mark"] = str(mark)
            payload["meta"] = meta
            conn.execute(
                "UPDATE trades SET payload=? WHERE trade_id=?",
                (json.dumps(payload), trade_id),
            )


def update_trail_stop(journal_path: str | Path, symbol: str, trail_stop: Decimal) -> None:
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
            meta["trail_stop"] = str(trail_stop)
            payload["meta"] = meta
            conn.execute(
                "UPDATE trades SET payload=? WHERE trade_id=?",
                (json.dumps(payload), trade_id),
            )

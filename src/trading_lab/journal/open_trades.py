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


def _open_rows_for_symbol(
    conn: sqlite3.Connection, sym: str
) -> list[tuple[str, str, Decimal, Decimal, dict[str, Any]]]:
    """Return open rows as (trade_id, entry_ts, entry_px, qty, payload) oldest→newest."""
    out: list[tuple[str, str, Decimal, Decimal, dict[str, Any]]] = []
    for trade_id, entry_ts, entry_px, qty, payload_raw in conn.execute(
        "SELECT trade_id, entry_ts, entry_px, qty, payload FROM trades "
        "WHERE upper(symbol)=? ORDER BY entry_ts ASC",
        (sym,),
    ):
        try:
            payload = json.loads(payload_raw or "{}")
        except json.JSONDecodeError:
            continue
        meta = payload.get("meta") or {}
        if meta.get("open") is not True:
            continue
        out.append(
            (
                str(trade_id),
                str(entry_ts or ""),
                Decimal(str(entry_px)),
                Decimal(str(qty)),
                payload,
            )
        )
    return out


def close_journal_trade(
    journal_path: str | Path,
    symbol: str,
    *,
    exit_px: Decimal,
    exit_reason: ExitReason,
    exit_ts: datetime | None = None,
    closed_by: str = "reconcile",
    trade_id: str | None = None,
) -> bool:
    """Close one canonical open row; supersede older open ghosts with zero P&L.

    Broker can only flatten a symbol once. Closing every open journal row for that
    symbol at full qty multiplies losses in Grafana (e.g. six ZYBT ghosts).
    """
    path = Path(journal_path)
    if not path.exists():
        return False
    sym = symbol.upper()
    ts = exit_ts or datetime.now(timezone.utc)
    closed = False
    with sqlite3.connect(path) as conn:
        opens = _open_rows_for_symbol(conn, sym)
        if not opens:
            return False
        open_ids = {tid for tid, *_rest in opens}
        # Prefer explicit trade_id; else latest open (matches load_open_plans).
        if trade_id and str(trade_id) in open_ids:
            canonical_id = str(trade_id)
        else:
            canonical_id = opens[-1][0]
        for tid, _entry_ts, entry, q, payload in opens:
            meta = dict(payload.get("meta") or {})
            if tid == canonical_id:
                pnl_usd = (exit_px - entry) * q
                pnl_pct = (
                    (pnl_usd / (entry * q) * Decimal("100")) if entry * q != 0 else Decimal("0")
                )
                meta["open"] = False
                meta["closed_by"] = closed_by
                meta.pop("ghost", None)
                reason = exit_reason
            else:
                # Ghost open — never book another full-qty exit against the same flatten.
                pnl_usd = Decimal("0")
                pnl_pct = Decimal("0")
                meta["open"] = False
                meta["closed_by"] = "superseded_ghost"
                meta["ghost"] = True
                reason = ExitReason.MANUAL
            payload["meta"] = meta
            payload["exit_px"] = str(exit_px)
            payload["exit_ts"] = ts.isoformat()
            payload["exit_reason"] = reason.value
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
                    reason.value,
                    str(pnl_usd),
                    str(pnl_pct),
                    json.dumps(payload),
                    tid,
                ),
            )
            if tid == canonical_id:
                closed = True
    return closed


def repair_ghost_reconcile_pnl(journal_path: str | Path) -> dict[str, Any]:
    """Zero historical duplicate reconcile closes that multiplied one broker exit.

    For each symbol, among closed rows with the same exit_px that were closed by
    reconcile (or already marked ghost), keep the latest entry_ts with real P&L
    and zero the rest.
    """
    path = Path(journal_path)
    if not path.exists():
        return {"ok": False, "detail": "missing_journal", "zeroed": 0}
    zeroed = 0
    with sqlite3.connect(path) as conn:
        rows = list(
            conn.execute(
                "SELECT trade_id, symbol, entry_ts, exit_px, pnl_usd, payload FROM trades "
                "ORDER BY symbol ASC, entry_ts ASC"
            )
        )
        # Group (symbol, exit_px) → candidate reconcile closes
        groups: dict[tuple[str, str], list[tuple[str, str, str, dict[str, Any]]]] = {}
        for trade_id, symbol, entry_ts, exit_px, pnl_usd, payload_raw in rows:
            if exit_px is None or str(exit_px).strip() == "":
                continue
            try:
                payload = json.loads(payload_raw or "{}")
            except json.JSONDecodeError:
                continue
            meta = payload.get("meta") or {}
            if meta.get("open") is True:
                continue
            closed_by = str(meta.get("closed_by") or "")
            if closed_by not in {"reconcile", "superseded_ghost"} and not meta.get("ghost"):
                # Also catch rows closed by reconcile path before closed_by existed:
                # multiple identical exit_px + non-zero pnl for same symbol.
                if closed_by:
                    continue
            try:
                if Decimal(str(pnl_usd or "0")) == 0 and not meta.get("ghost"):
                    # Already zero and not part of a ghost cluster — skip unless reconcile.
                    if closed_by != "reconcile":
                        continue
            except Exception:  # noqa: BLE001
                pass
            key = (str(symbol).upper(), str(exit_px))
            groups.setdefault(key, []).append(
                (str(trade_id), str(entry_ts or ""), str(pnl_usd or "0"), payload)
            )

        for (_sym, _exit_px), members in groups.items():
            if len(members) < 2:
                continue
            # Keep latest entry_ts; prefer non-zero pnl as the survivor when tied.
            members_sorted = sorted(members, key=lambda m: m[1])
            keep_id = members_sorted[-1][0]
            for tid, _ets, _pnl, payload in members_sorted:
                if tid == keep_id:
                    meta = dict(payload.get("meta") or {})
                    meta.pop("ghost", None)
                    if meta.get("closed_by") == "superseded_ghost":
                        meta["closed_by"] = "reconcile"
                    payload["meta"] = meta
                    conn.execute(
                        "UPDATE trades SET payload=? WHERE trade_id=?",
                        (json.dumps(payload), tid),
                    )
                    continue
                meta = dict(payload.get("meta") or {})
                meta["open"] = False
                meta["ghost"] = True
                meta["closed_by"] = "superseded_ghost"
                payload["meta"] = meta
                payload["pnl_usd"] = "0"
                payload["pnl_pct"] = "0"
                payload["exit_reason"] = ExitReason.MANUAL.value
                conn.execute(
                    """
                    UPDATE trades SET pnl_usd=?, pnl_pct=?, exit_reason=?, payload=?
                    WHERE trade_id=?
                    """,
                    ("0", "0", ExitReason.MANUAL.value, json.dumps(payload), tid),
                )
                zeroed += 1
    return {"ok": True, "zeroed": zeroed}


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
        opens = _open_rows_for_symbol(conn, sym)
        if not opens:
            return
        trade_id, _ets, _entry, _q, payload = opens[-1]
        meta = dict(payload.get("meta") or {})
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
        opens = _open_rows_for_symbol(conn, sym)
        if not opens:
            return
        trade_id, _ets, _entry, _q, payload = opens[-1]
        meta = dict(payload.get("meta") or {})
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
        opens = _open_rows_for_symbol(conn, sym)
        if not opens:
            return
        trade_id, _ets, _entry, _q, payload = opens[-1]
        meta = dict(payload.get("meta") or {})
        meta["trail_stop"] = str(trail_stop)
        payload["meta"] = meta
        conn.execute(
            "UPDATE trades SET payload=? WHERE trade_id=?",
            (json.dumps(payload), trade_id),
        )

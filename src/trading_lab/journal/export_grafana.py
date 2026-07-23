"""Export journal tables to Grafana-friendly CSV (Infinity / CSV datasource)."""

from __future__ import annotations

import csv
import json
import sqlite3
from pathlib import Path
from typing import Any

TRADE_COLUMNS = (
    "trade_id",
    "run_id",
    "found_by_agent",
    "agent_id",
    "symbol",
    "side",
    "mode",
    "entry_ts",
    "entry_px",
    "exit_ts",
    "exit_px",
    "qty",
    "pnl_usd",
    "pnl_pct",
    "pnl_booked_usd",
    "is_closed",
    "status",
    "ghost",
    "exit_reason",
    "bars_held",
    "hold_summary",
    "fill_model",
    "payload",
)

SKIP_COLUMNS = (
    "event_id",
    "run_id",
    "found_by_agent",
    "agent_id",
    "symbol",
    "ts",
    "mode",
    "skip_reason",
    "detail",
    "payload",
)

_TABLE_COLUMNS = {
    "trades": TRADE_COLUMNS,
    "skips": SKIP_COLUMNS,
}


def empty_csv_header(table: str) -> bytes:
    """Header-only CSV body so Infinity panels work before first persist."""
    columns = _TABLE_COLUMNS.get(table)
    if not columns:
        raise ValueError(f"unsupported table: {table}")
    return (",".join(columns) + "\n").encode("utf-8")


def trade_row_status(payload_raw: str | None) -> tuple[str, str, str, str]:
    """Return (status, ghost, is_closed, pnl_booked_usd_override_or_empty).

    status: open | closed | ghost
    ghost / is_closed: \"true\"/\"false\" and \"1\"/\"0\"
    pnl_booked override: empty string means use column pnl_usd when closed.
    """
    try:
        payload = json.loads(payload_raw or "{}")
    except json.JSONDecodeError:
        payload = {}
    meta = payload.get("meta") or {}
    if meta.get("open") is True:
        return "open", "false", "0", "0"
    if meta.get("ghost") or str(meta.get("closed_by") or "") == "superseded_ghost":
        return "ghost", "true", "0", "0"
    return "closed", "false", "1", ""


def _enrich_trade_row(row: sqlite3.Row) -> dict[str, Any]:
    keys = set(row.keys())
    out: dict[str, Any] = {col: (row[col] if col in keys else "") for col in TRADE_COLUMNS}
    status, ghost, is_closed, booked_override = trade_row_status(
        row["payload"] if "payload" in keys else None
    )
    out["status"] = status
    out["ghost"] = ghost
    out["is_closed"] = is_closed
    if booked_override != "":
        out["pnl_booked_usd"] = booked_override
    else:
        out["pnl_booked_usd"] = out.get("pnl_usd") or "0"
    return out


def export_journal_csv(db_path: str | Path, out_dir: str | Path) -> dict[str, Path]:
    """Write trades.csv + skips.csv for Grafana Infinity / CSV datasource.

    Empty tables still get a header row so Infinity can parse schema.
    """
    db_path = Path(db_path)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    paths: dict[str, Path] = {}
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        for table, columns in _TABLE_COLUMNS.items():
            # table names are fixed literals, not user input
            rows = conn.execute(f"SELECT * FROM {table}").fetchall()  # nosec B608
            path = out_dir / f"{table}.csv"
            with path.open("w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=columns, extrasaction="ignore")
                writer.writeheader()
                for row in rows:
                    if table == "trades":
                        writer.writerow(_enrich_trade_row(row))
                    else:
                        writer.writerow(
                            {col: row[col] if col in row.keys() else "" for col in columns}
                        )
            paths[table] = path
    return paths

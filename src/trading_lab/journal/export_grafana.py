"""Export journal tables to Grafana-friendly CSV (Infinity / CSV datasource)."""

from __future__ import annotations

import csv
import sqlite3
from pathlib import Path

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
                    writer.writerow({col: row[col] if col in row.keys() else "" for col in columns})
            paths[table] = path
    return paths

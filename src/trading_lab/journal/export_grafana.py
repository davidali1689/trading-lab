"""Export journal tables to Grafana-friendly CSV (SQLite / Postgres datasource)."""

from __future__ import annotations

import csv
import sqlite3
from pathlib import Path


def export_journal_csv(db_path: str | Path, out_dir: str | Path) -> dict[str, Path]:
    """Write trades.csv + skips.csv for Grafana Infinity / CSV datasource."""
    db_path = Path(db_path)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    paths: dict[str, Path] = {}
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        for table in ("trades", "skips"):
            # table names are fixed literals, not user input
            rows = conn.execute(f"SELECT * FROM {table}").fetchall()  # nosec B608
            path = out_dir / f"{table}.csv"
            if not rows:
                path.write_text("", encoding="utf-8")
                paths[table] = path
                continue
            fieldnames = rows[0].keys()
            with path.open("w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                writer.writeheader()
                for row in rows:
                    writer.writerow(dict(row))
            paths[table] = path
    return paths

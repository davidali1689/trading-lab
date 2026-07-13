"""SQLite trade journal — local source of truth before Dynamo/Postgres."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Iterable

from trading_lab.schemas.trades import SkipEvent, TradeRecord


class SqliteJournal:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init()

    def _conn(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path)

    def _init(self) -> None:
        with self._conn() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS trades (
                  trade_id TEXT PRIMARY KEY,
                  run_id TEXT NOT NULL,
                  found_by_agent TEXT NOT NULL,
                  agent_id TEXT NOT NULL,
                  symbol TEXT NOT NULL,
                  side TEXT NOT NULL,
                  mode TEXT NOT NULL,
                  entry_ts TEXT NOT NULL,
                  entry_px TEXT NOT NULL,
                  exit_ts TEXT NOT NULL,
                  exit_px TEXT NOT NULL,
                  qty TEXT NOT NULL,
                  pnl_usd TEXT NOT NULL,
                  pnl_pct TEXT NOT NULL,
                  exit_reason TEXT NOT NULL,
                  bars_held INTEGER,
                  hold_summary TEXT,
                  fill_model TEXT,
                  payload TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_trades_agent ON trades(found_by_agent);
                CREATE TABLE IF NOT EXISTS skips (
                  event_id TEXT PRIMARY KEY,
                  run_id TEXT NOT NULL,
                  found_by_agent TEXT NOT NULL,
                  agent_id TEXT NOT NULL,
                  symbol TEXT NOT NULL,
                  ts TEXT NOT NULL,
                  mode TEXT NOT NULL,
                  skip_reason TEXT NOT NULL,
                  detail TEXT,
                  payload TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_skips_agent ON skips(found_by_agent);
                """
            )

    def write_trade(self, trade: TradeRecord) -> None:
        payload = trade.model_dump(mode="json")
        with self._conn() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO trades (
                  trade_id, run_id, found_by_agent, agent_id, symbol, side, mode,
                  entry_ts, entry_px, exit_ts, exit_px, qty, pnl_usd, pnl_pct,
                  exit_reason, bars_held, hold_summary, fill_model, payload
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    str(trade.trade_id),
                    str(trade.run_id),
                    trade.found_by_agent,
                    trade.agent_id or trade.found_by_agent,
                    trade.symbol,
                    trade.side.value,
                    trade.mode.value,
                    trade.entry_ts.isoformat(),
                    str(trade.entry_px),
                    trade.exit_ts.isoformat(),
                    str(trade.exit_px),
                    str(trade.qty),
                    str(trade.pnl_usd),
                    str(trade.pnl_pct),
                    trade.exit_reason.value,
                    trade.bars_held,
                    trade.hold_plan.summary if trade.hold_plan else None,
                    trade.fill_model,
                    json.dumps(payload),
                ),
            )

    def write_skip(self, skip: SkipEvent) -> None:
        payload = skip.model_dump(mode="json")
        with self._conn() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO skips (
                  event_id, run_id, found_by_agent, agent_id, symbol, ts, mode,
                  skip_reason, detail, payload
                ) VALUES (?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    str(skip.event_id),
                    str(skip.run_id),
                    skip.found_by_agent,
                    skip.agent_id or skip.found_by_agent,
                    skip.symbol,
                    skip.ts.isoformat(),
                    skip.mode.value,
                    skip.skip_reason.value,
                    skip.detail,
                    json.dumps(payload),
                ),
            )

    def write_trades(self, trades: Iterable[TradeRecord]) -> None:
        for t in trades:
            self.write_trade(t)

    def write_skips(self, skips: Iterable[SkipEvent]) -> None:
        for s in skips:
            self.write_skip(s)

    def count_trades(self) -> int:
        with self._conn() as conn:
            row = conn.execute("SELECT COUNT(*) FROM trades").fetchone()
            return int(row[0])

"""EOD flatten — close intraday sniper paper positions; leave swing overnight."""

from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path
from typing import Any

from trading_lab.broker.alpaca import AlpacaPaperBroker
from trading_lab.config.secrets import has_alpaca_keys

logger = logging.getLogger("trading_lab.eod_flatten")

SNIPER_AGENTS = frozenset({"large_cap_sniper", "mid_cap_sniper", "speculative_sniper"})


def flatten_sniper_paper(journal_path: str) -> list[dict[str, Any]]:
    """Close Alpaca paper positions from open sniper journal rows only."""
    if not has_alpaca_keys():
        return [{"ok": False, "detail": "no_alpaca_keys"}]

    symbols: set[str] = set()
    path = Path(journal_path)
    if path.exists():
        with sqlite3.connect(path) as conn:
            for row in conn.execute("SELECT symbol, found_by_agent, payload FROM trades"):
                symbol, agent, payload_raw = row
                if agent not in SNIPER_AGENTS:
                    continue
                try:
                    payload = json.loads(payload_raw or "{}")
                except json.JSONDecodeError:
                    continue
                meta = payload.get("meta") or {}
                if meta.get("open") is True:
                    symbols.add(str(symbol).upper())

    if not symbols:
        return [{"ok": True, "detail": "no_open_sniper_positions"}]

    broker = AlpacaPaperBroker()
    out: list[dict[str, Any]] = []
    for sym in sorted(symbols):
        try:
            broker.close_position(sym)
            out.append({"symbol": sym, "ok": True})
            logger.info("EOD flattened sniper position %s", sym)
        except Exception as exc:  # noqa: BLE001
            out.append({"symbol": sym, "ok": False, "detail": str(exc)})
            logger.exception("EOD flatten failed for %s", sym)
    return out

"""Print today's watchlist symbols for the morning research run.

The worker writes watchlists/{day}.json at 8:00 AM Eastern. This script
refuses yesterday's latest.json when today's file is missing.

Usage:
    uv run python scripts/show_watchlist.py
"""

from __future__ import annotations

import json

from trading_lab.schedule.market_clock import now_et
from trading_lab.selection.watchlist import load_watchlist_for_day


def main() -> int:
    day = now_et().date().isoformat()
    doc = load_watchlist_for_day(day)
    if not doc.symbols:
        print(json.dumps({"ok": False, "day": day, "detail": doc.detail, "symbols": []}))
        return 1
    print(json.dumps({"ok": True, "day": day, "symbols": doc.symbols}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

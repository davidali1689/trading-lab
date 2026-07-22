"""Finnhub company-news catalyst for sniper paper path."""

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

logger = logging.getLogger("trading_lab.catalysts.finnhub_news")


def has_finnhub_key() -> bool:
    return bool(os.environ.get("FINNHUB_API_KEY", "").strip())


def symbol_has_recent_news(
    symbol: str,
    *,
    lookback_hours: int = 48,
    api_key: str | None = None,
) -> bool:
    """True if Finnhub reports ≥1 company-news item in the lookback window."""
    key = (api_key or os.environ.get("FINNHUB_API_KEY") or "").strip()
    if not key:
        return False
    end = datetime.now(timezone.utc)
    start = end - timedelta(hours=lookback_hours)
    qs = urllib.parse.urlencode(
        {
            "symbol": symbol.upper(),
            "from": start.strftime("%Y-%m-%d"),
            "to": end.strftime("%Y-%m-%d"),
            "token": key,
        }
    )
    url = f"https://finnhub.io/api/v1/company-news?{qs}"
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:  # nosec B310
            raw = resp.read().decode()
            rows = json.loads(raw) if raw else []
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, json.JSONDecodeError) as exc:
        logger.warning("finnhub news failed for %s: %s", symbol, exc)
        return False
    if not isinstance(rows, list):
        return False
    cutoff = start.timestamp()
    for row in rows:
        if not isinstance(row, dict):
            continue
        ts = row.get("datetime")
        try:
            if ts is not None and float(ts) >= cutoff:
                return True
        except (TypeError, ValueError):
            continue
    return bool(rows)  # same-day news without usable ts still counts

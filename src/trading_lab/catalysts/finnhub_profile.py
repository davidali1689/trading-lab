"""Finnhub company profile2 — market cap for sniper routing."""

from __future__ import annotations

import json
import logging
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from decimal import Decimal, InvalidOperation

logger = logging.getLogger("trading_lab.catalysts.finnhub_profile")

# Finnhub marketCapitalization is in millions USD.
_MILLIONS = Decimal("1000000")
# Cap changes slowly; cache across paper ticks to stay under rate limits.
_CACHE_TTL_SEC = 6 * 60 * 60
_cache: dict[str, tuple[float, Decimal | None]] = {}


def clear_market_cap_cache() -> None:
    _cache.clear()


def fetch_market_cap_usd(
    symbol: str,
    *,
    api_key: str | None = None,
    now: float | None = None,
) -> Decimal | None:
    """Return market cap in USD, or None if unknown / unavailable."""
    sym = symbol.upper().strip()
    if not sym:
        return None
    key = (api_key or os.environ.get("FINNHUB_API_KEY") or "").strip()
    if not key:
        return None

    ts = time.time() if now is None else now
    cached = _cache.get(sym)
    if cached is not None and (ts - cached[0]) < _CACHE_TTL_SEC:
        return cached[1]

    qs = urllib.parse.urlencode({"symbol": sym, "token": key})
    url = f"https://finnhub.io/api/v1/stock/profile2?{qs}"
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:  # nosec B310
            raw = resp.read().decode()
            payload = json.loads(raw) if raw else {}
    except (
        urllib.error.URLError,
        urllib.error.HTTPError,
        TimeoutError,
        json.JSONDecodeError,
    ) as exc:
        logger.warning("finnhub profile2 failed for %s: %s", sym, exc)
        _cache[sym] = (ts, None)
        return None

    if not isinstance(payload, dict):
        _cache[sym] = (ts, None)
        return None

    raw_cap = payload.get("marketCapitalization")
    if raw_cap is None or raw_cap == "":
        _cache[sym] = (ts, None)
        return None
    try:
        millions = Decimal(str(raw_cap))
    except (InvalidOperation, ValueError):
        _cache[sym] = (ts, None)
        return None
    if millions <= 0:
        _cache[sym] = (ts, None)
        return None

    cap = (millions * _MILLIONS).quantize(Decimal("1"))
    _cache[sym] = (ts, cap)
    return cap

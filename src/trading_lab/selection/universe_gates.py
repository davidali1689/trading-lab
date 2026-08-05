"""Universe entry gates — extended day-gainers, leveraged/ETF products, large cluster.

Locked from 2026-08-05 session lessons (AMIX chase, PLTU leveraged ETF, SPCX-as-large,
correlated large morning stops).
"""

from __future__ import annotations

import os
import re
from decimal import Decimal
from typing import Any

# Known single-stock / multi-X leveraged products seen on screeners.
LEVERAGED_ETF_SYMBOLS = frozenset(
    {
        "PLTU",
        "PLTG",
        "PLTL",
        "PLTA",
        "PTIR",
        "TQQQ",
        "SQQQ",
        "SOXL",
        "SOXS",
        "SPXL",
        "SPXS",
        "UPRO",
        "SPXU",
        "TNA",
        "TZA",
        "LABU",
        "LABD",
        "FNGU",
        "FNGD",
        "NVDL",
        "NVDX",
        "TSLL",
        "CONL",
        "AAPU",
        "MSFU",
        "AMZU",
        "METU",
        "GGLL",
    }
)

_NAME_LEVERAGED = re.compile(
    r"\b(2x|3x|-2x|-3x|leveraged|ultra(bull|bear)?|daily\s+(bull|bear)|bull\s+3x|bear\s+3x)\b",
    re.IGNORECASE,
)
_NAME_ETF = re.compile(r"\betf\b", re.IGNORECASE)


def max_day_gain_pct() -> Decimal:
    raw = os.environ.get("MAX_DAY_GAIN_PCT", "40")
    try:
        return Decimal(raw)
    except Exception:  # noqa: BLE001
        return Decimal("40")


def max_open_large_cap() -> int:
    try:
        return max(1, int(os.environ.get("MAX_OPEN_LARGE_CAP", "2")))
    except ValueError:
        return 2


def day_gain_too_extended(percent_change: Decimal | None) -> bool:
    """True when screener day-change is already at/above the chase ceiling."""
    if percent_change is None:
        return False
    return percent_change >= max_day_gain_pct()


def is_disallowed_product(symbol: str, *, meta: Any | None = None) -> bool:
    """Reject leveraged products and ETFs (snipers trade common stock, not products)."""
    sym = (symbol or "").upper().strip()
    if not sym:
        return True
    if sym in LEVERAGED_ETF_SYMBOLS:
        return True
    name = ""
    if meta is not None:
        name = str(getattr(meta, "name", None) or "")
    if name and _NAME_LEVERAGED.search(name):
        return True
    if name and _NAME_ETF.search(name):
        return True
    return False

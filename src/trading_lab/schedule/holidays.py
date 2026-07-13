"""NYSE full-day holidays (no overnight early-close nuance in v0).

Update yearly. Source of truth for skipping Mon–Fri cron work.
"""

from __future__ import annotations

from datetime import date


# Observed / full closes — extend each year
NYSE_HOLIDAYS: set[date] = {
    # 2026
    date(2026, 1, 1),  # New Year's Day
    date(2026, 1, 19),  # MLK
    date(2026, 2, 16),  # Presidents
    date(2026, 4, 3),  # Good Friday
    date(2026, 5, 25),  # Memorial
    date(2026, 6, 19),  # Juneteenth
    date(2026, 7, 3),  # Independence (observed)
    date(2026, 9, 7),  # Labor
    date(2026, 11, 26),  # Thanksgiving
    date(2026, 12, 25),  # Christmas
    # 2027 (seed)
    date(2027, 1, 1),
    date(2027, 1, 18),
    date(2027, 2, 15),
    date(2027, 3, 26),
    date(2027, 5, 31),
    date(2027, 6, 18),
    date(2027, 7, 5),
    date(2027, 9, 6),
    date(2027, 11, 25),
    date(2027, 12, 24),
}


def is_nyse_holiday(d: date) -> bool:
    return d in NYSE_HOLIDAYS

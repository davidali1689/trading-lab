"""US equity session clock (America/New_York).

Day shape for next-day readiness:
  08:00  premarket prep (no entries)
  09:30–16:00  RTH ticks (entries allowed if gates pass)
  15:30–16:00  power hour (swing preference)
  16:05  eod flatten + journal persist
  18:00  postmarket prep for *tomorrow* (watchlist notes — still no entries)

Trading ticks until 18:00 does NOT help next-day setups; postmarket prep does.
"""

from __future__ import annotations

from datetime import datetime, time, timedelta
from enum import StrEnum
from zoneinfo import ZoneInfo

from trading_lab.schedule.holidays import is_nyse_holiday

ET = ZoneInfo("America/New_York")


class SessionPhase(StrEnum):
    CLOSED = "closed"
    PREMARKET = "premarket"
    OPEN = "open"
    POWER_HOUR = "power_hour"
    EOD = "eod"
    POSTMARKET = "postmarket"  # 16:30–18:00 prep for next day


PREMARKET_START = time(8, 0)  # 08:00 ET
RTH_OPEN = time(9, 30)
POWER_HOUR_START = time(15, 30)
RTH_CLOSE = time(16, 0)
EOD_END = time(16, 30)
POSTMARKET_END = time(18, 0)  # stop process for the day

SCHEDULE_TIMEZONE = "America/New_York"

SCHEDULES = {
    "premarket": {
        "description": "08:00 ET Mon–Fri prep (no entries)",
        "schedule_expression": "cron(0 8 ? * MON-FRI *)",
        "input": {"phase": "premarket"},
    },
    "rth_open_hour": {
        "description": "Every minute 09:30–09:59 ET",
        "schedule_expression": "cron(30-59 9 ? * MON-FRI *)",
        "input": {"phase": "tick"},
    },
    "rth_mid": {
        "description": "Every minute 10:00–15:59 ET",
        "schedule_expression": "cron(* 10-15 ? * MON-FRI *)",
        "input": {"phase": "tick"},
    },
    "eod": {
        "description": "16:05 ET flatten + persist journal",
        "schedule_expression": "cron(5 16 ? * MON-FRI *)",
        "input": {"phase": "eod"},
    },
    "postmarket": {
        "description": "18:00 ET next-day prep (no entries) then idle",
        "schedule_expression": "cron(0 18 ? * MON-FRI *)",
        "input": {"phase": "postmarket"},
    },
}


def now_et(ts: datetime | None = None) -> datetime:
    if ts is None:
        return datetime.now(tz=ET)
    if ts.tzinfo is None:
        return ts.replace(tzinfo=ET)
    return ts.astimezone(ET)


def is_session_day(ts: datetime | None = None) -> bool:
    t = now_et(ts)
    if t.weekday() >= 5:
        return False
    return not is_nyse_holiday(t.date())


def phase_at(ts: datetime | None = None) -> SessionPhase:
    t = now_et(ts)
    if not is_session_day(t):
        return SessionPhase.CLOSED
    clock = t.time()
    if PREMARKET_START <= clock < RTH_OPEN:
        return SessionPhase.PREMARKET
    if RTH_OPEN <= clock < POWER_HOUR_START:
        return SessionPhase.OPEN
    if POWER_HOUR_START <= clock < RTH_CLOSE:
        return SessionPhase.POWER_HOUR
    if RTH_CLOSE <= clock < EOD_END:
        return SessionPhase.EOD
    if EOD_END <= clock < POSTMARKET_END:
        return SessionPhase.POSTMARKET
    return SessionPhase.CLOSED


def sniper_ticks_allowed(ts: datetime | None = None) -> bool:
    return phase_at(ts) in {SessionPhase.OPEN, SessionPhase.POWER_HOUR}


def swing_power_hour(ts: datetime | None = None) -> bool:
    return phase_at(ts) == SessionPhase.POWER_HOUR


def should_run_premarket(ts: datetime | None = None) -> bool:
    return phase_at(ts) == SessionPhase.PREMARKET


def should_run_eod(ts: datetime | None = None) -> bool:
    return phase_at(ts) == SessionPhase.EOD


def should_run_postmarket(ts: datetime | None = None) -> bool:
    return phase_at(ts) == SessionPhase.POSTMARKET


def next_premarket_after(ts: datetime | None = None) -> datetime:
    t = now_et(ts)
    candidate = t.replace(hour=8, minute=0, second=0, microsecond=0)
    if t.time() >= PREMARKET_START:
        candidate = candidate + timedelta(days=1)
    while not is_session_day(candidate):
        candidate = candidate + timedelta(days=1)
    return candidate


def process_window_label() -> str:
    return "08:00–18:00 ET session day (entries only 09:30–16:00)"

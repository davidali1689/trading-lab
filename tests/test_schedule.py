from datetime import date, datetime
from zoneinfo import ZoneInfo

from trading_lab.schedule import (
    SCHEDULES,
    SessionPhase,
    entries_enabled,
    is_nyse_holiday,
    phase_at,
    process_window_label,
    sniper_ticks_allowed,
)

ET = ZoneInfo("America/New_York")


def test_weekday_open():
    ts = datetime(2026, 7, 13, 10, 0, tzinfo=ET)
    assert phase_at(ts) == SessionPhase.OPEN
    assert sniper_ticks_allowed(ts)


def test_premarket_starts_8am():
    ts = datetime(2026, 7, 13, 8, 10, tzinfo=ET)
    assert phase_at(ts) == SessionPhase.PREMARKET
    assert not sniper_ticks_allowed(ts)


def test_power_hour_eod_postmarket():
    assert phase_at(datetime(2026, 7, 13, 15, 45, tzinfo=ET)) == SessionPhase.POWER_HOUR
    assert phase_at(datetime(2026, 7, 13, 16, 10, tzinfo=ET)) == SessionPhase.EOD
    assert phase_at(datetime(2026, 7, 13, 17, 0, tzinfo=ET)) == SessionPhase.POSTMARKET
    assert not sniper_ticks_allowed(datetime(2026, 7, 13, 17, 0, tzinfo=ET))


def test_after_6pm_closed():
    assert phase_at(datetime(2026, 7, 13, 18, 0, tzinfo=ET)) == SessionPhase.CLOSED


def test_weekend_and_holiday_closed():
    assert phase_at(datetime(2026, 7, 12, 10, 0, tzinfo=ET)) == SessionPhase.CLOSED
    assert is_nyse_holiday(date(2026, 7, 3))
    assert phase_at(datetime(2026, 7, 3, 10, 0, tzinfo=ET)) == SessionPhase.CLOSED


def test_schedules_include_postmarket_and_8am():
    assert SCHEDULES["premarket"]["schedule_expression"].startswith("cron(0 8")
    assert "postmarket" in SCHEDULES
    assert "18:00" in process_window_label() or "18:00" in SCHEDULES["postmarket"]["description"]


def test_kill_switch_env(monkeypatch):
    monkeypatch.delenv("KILL_SWITCH", raising=False)
    assert entries_enabled()
    monkeypatch.setenv("KILL_SWITCH", "1")
    assert not entries_enabled()

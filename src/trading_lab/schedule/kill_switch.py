"""Global kill switch — disable ENTERs without tearing down schedules."""

from __future__ import annotations

import os


def entries_enabled() -> bool:
    """False when KILL_SWITCH=1/true/yes — ticks still run but cannot ENTER."""
    raw = os.environ.get("KILL_SWITCH", "0").strip().lower()
    return raw not in {"1", "true", "yes", "on"}


def kill_switch_reason() -> str:
    return "KILL_SWITCH enabled — entries blocked"

"""Dead-code cleanup: unused symbols gone; wrong notes / idle helpers fixed."""

from __future__ import annotations

import trading_lab.config.vendors as vendors
import trading_lab.improvement.scorecard as scorecard_mod
from trading_lab.agents.sniper.mid_cap import MID_CAP_SNIPER
from trading_lab.improvement.overlay import load_overlay, write_overlay


def test_unused_data_role_enum_removed() -> None:
    assert not hasattr(vendors, "DataRole")


def test_unused_run_and_persist_scorecard_wrapper_removed() -> None:
    assert not hasattr(scorecard_mod, "run_and_persist_scorecard")


def test_mid_cap_notes_match_paper_catalyst_requirement() -> None:
    """Notes must not claim paper catalyst is relaxed when the gate is required."""
    assert MID_CAP_SNIPER.require_catalyst_in_paper is True
    joined = " ".join(MID_CAP_SNIPER.notes).lower()
    assert "catalyst required" in joined and "paper" in joined
    assert "paper/backtest: catalyst relaxed" not in joined


def test_overlay_helpers_work_without_bucket(monkeypatch) -> None:
    """Overlay load/write stay as intentional green-light API (not dead)."""
    monkeypatch.delenv("JOURNAL_S3_BUCKET", raising=False)
    assert load_overlay() is None
    out = write_overlay({"agent_id": "large_cap_sniper", "changes": []})
    assert out["ok"] is False
    assert "JOURNAL_S3_BUCKET" in out["detail"]

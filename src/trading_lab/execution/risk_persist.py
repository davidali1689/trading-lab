"""Persist RiskGate state across ticks (daily loss + cool-off)."""

from __future__ import annotations

import json
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from trading_lab.execution.risk_gate import RiskGate, RiskGateConfig, RiskGateState


def risk_state_path(journal_path: str | Path) -> Path:
    p = Path(journal_path)
    return p.with_name(p.stem + "-risk-state.json")


def load_risk_gate(
    journal_path: str | Path,
    *,
    config: RiskGateConfig | None = None,
) -> RiskGate:
    gate = RiskGate(config=config or RiskGateConfig())
    path = risk_state_path(journal_path)
    if not path.exists():
        return gate
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return gate
    state = raw.get("state") or {}
    day_s = state.get("day")
    cool = state.get("cooling_off_until")
    last_stop = state.get("last_stop_ts")
    gate.state = RiskGateState(
        open_positions=int(state.get("open_positions") or 0),
        day=date.fromisoformat(day_s) if day_s else None,
        realized_pnl_today=Decimal(str(state.get("realized_pnl_today") or "0")),
        last_stop_ts=datetime.fromisoformat(last_stop) if last_stop else None,
        cooling_off_until=datetime.fromisoformat(cool) if cool else None,
    )
    return gate


def save_risk_gate(journal_path: str | Path, gate: RiskGate) -> None:
    path = risk_state_path(journal_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    state = gate.state
    payload = {
        "state": {
            "open_positions": state.open_positions,
            "day": state.day.isoformat() if state.day else None,
            "realized_pnl_today": str(state.realized_pnl_today),
            "last_stop_ts": state.last_stop_ts.isoformat() if state.last_stop_ts else None,
            "cooling_off_until": (
                state.cooling_off_until.isoformat() if state.cooling_off_until else None
            ),
        }
    }
    path.write_text(json.dumps(payload), encoding="utf-8")

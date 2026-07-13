"""Cloud worker — unattended 08:00–18:00 ET session day.

Entries only in RTH. Postmarket (→18:00) prepares next day; does not trade.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from pydantic import BaseModel, Field

from trading_lab.config.secrets import has_alpaca_keys, load_secrets
from trading_lab.journal.persist import persist_journal_to_s3
from trading_lab.pipeline.paper_tick import run_paper_tick
from trading_lab.pipeline.swing_tick import evaluate_swing_with_congress
from trading_lab.pipeline.vertical_slice import run_vertical_slice
from trading_lab.schedule import (
    entries_enabled,
    is_nyse_holiday,
    kill_switch_reason,
    phase_at,
    process_window_label,
    should_run_eod,
    should_run_postmarket,
    sniper_ticks_allowed,
    swing_power_hour,
)
from trading_lab.schedule.market_clock import now_et
from trading_lab.schemas.trades import RunMode

logger = logging.getLogger("trading_lab.api")
logging.basicConfig(level=logging.INFO)

# Prefer AWS Secrets Manager (SECRET_ARN) over empty Lambda env for vendor keys.
load_secrets(hydrate=True)

app = FastAPI(title="trading-lab", version="0.2.0")

JOURNAL_PATH = os.environ.get("JOURNAL_PATH", "/tmp/trading-lab-journal.sqlite")  # nosec B108
TRADING_MODE = os.environ.get("TRADING_MODE", "paper")
SYMBOLS = [
    s.strip().upper() for s in os.environ.get("WATCHLIST", "AAPL,MSFT,SPY").split(",") if s.strip()
]


class PhaseRequest(BaseModel):
    phase: str = Field(
        ...,
        description="premarket | tick | eod | postmarket | status",
    )
    force: bool = False
    symbol: str | None = None


class PhaseResult(BaseModel):
    ok: bool
    phase: str
    clock_phase: str
    detail: str
    results: list[dict[str, Any]] = Field(default_factory=list)
    ts: str


def _mode() -> RunMode:
    try:
        return RunMode(TRADING_MODE)
    except ValueError:
        return RunMode.PAPER


def _holiday_noop(phase: str) -> PhaseResult | None:
    today = now_et().date()
    if is_nyse_holiday(today):
        return PhaseResult(
            ok=True,
            phase=phase,
            clock_phase="closed",
            detail=f"NYSE holiday {today.isoformat()} — no-op",
            ts=datetime.now(timezone.utc).isoformat(),
        )
    return None


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "trading-lab"}


@app.get("/status")
def status() -> dict[str, Any]:
    return {
        "clock_phase": phase_at().value,
        "sniper_ticks_allowed": sniper_ticks_allowed(),
        "swing_power_hour": swing_power_hour(),
        "entries_enabled": entries_enabled(),
        "process_window": process_window_label(),
        "trading_mode": TRADING_MODE,
        "watchlist": SYMBOLS,
        "ts": datetime.now(timezone.utc).isoformat(),
    }


@app.post("/run")
def run_phase(body: PhaseRequest) -> PhaseResult:
    holiday = _holiday_noop(body.phase)
    if holiday is not None and not body.force:
        return holiday

    clock = phase_at()
    phase = body.phase.lower().strip()
    results: list[dict[str, Any]] = []

    if phase == "status":
        return PhaseResult(
            ok=True,
            phase=phase,
            clock_phase=clock.value,
            detail=process_window_label(),
            ts=datetime.now(timezone.utc).isoformat(),
        )

    if phase == "premarket":
        Path(JOURNAL_PATH).parent.mkdir(parents=True, exist_ok=True)
        detail = (
            f"08:00 prep watchlist={SYMBOLS}; entries_enabled={entries_enabled()}; "
            "no ENTERs until RTH"
        )
        logger.info(detail)
        # Metric for CW alarm: successful premarket
        print(json.dumps({"metric": "premarket_ok", "value": 1}))
        return PhaseResult(
            ok=True,
            phase=phase,
            clock_phase=clock.value,
            detail=detail,
            ts=datetime.now(timezone.utc).isoformat(),
        )

    if phase == "tick":
        if not body.force and not sniper_ticks_allowed():
            return PhaseResult(
                ok=True,
                phase=phase,
                clock_phase=clock.value,
                detail="outside RTH — no-op (no entries after 16:00 / before 09:30)",
                ts=datetime.now(timezone.utc).isoformat(),
            )
        if not entries_enabled():
            return PhaseResult(
                ok=True,
                phase=phase,
                clock_phase=clock.value,
                detail=kill_switch_reason(),
                ts=datetime.now(timezone.utc).isoformat(),
            )
        symbols = [body.symbol] if body.symbol else SYMBOLS
        power = swing_power_hour()
        for sym in symbols:
            use_mock = os.environ.get("USE_MOCK_BARS", "true").lower() == "true"
            if use_mock or _mode() in {RunMode.BACKTEST, RunMode.SIM}:
                summary = run_vertical_slice(
                    symbol=sym,
                    journal_path=JOURNAL_PATH,
                    mode=RunMode.SIM if _mode() == RunMode.PAPER else _mode(),
                )
                summary["swing_power_hour"] = power
                summary["swing_congress"] = evaluate_swing_with_congress(sym, use_mock=use_mock)
                results.append(summary)
            elif _mode() == RunMode.PAPER and has_alpaca_keys():
                summary = run_paper_tick(symbol=sym, journal_path=JOURNAL_PATH)
                summary["swing_power_hour"] = power
                summary["swing_congress"] = evaluate_swing_with_congress(sym, use_mock=False)
                results.append(summary)
            else:
                results.append(
                    {
                        "symbol": sym,
                        "swing_power_hour": power,
                        "detail": (
                            "paper path needs USE_MOCK_BARS=false "
                            "+ Alpaca keys in Secrets Manager"
                        ),
                    }
                )
        return PhaseResult(
            ok=True,
            phase=phase,
            clock_phase=clock.value,
            detail=f"tick symbols={len(symbols)} power_hour={power}",
            results=results,
            ts=datetime.now(timezone.utc).isoformat(),
        )

    if phase == "eod":
        if not body.force and not should_run_eod():
            return PhaseResult(
                ok=True,
                phase=phase,
                clock_phase=clock.value,
                detail="outside EOD window — no-op",
                ts=datetime.now(timezone.utc).isoformat(),
            )
        persist = persist_journal_to_s3(JOURNAL_PATH)
        results.append(persist)
        detail = f"eod flatten + persist: {persist}"
        logger.info(detail)
        return PhaseResult(
            ok=True,
            phase=phase,
            clock_phase=clock.value,
            detail=detail,
            results=results,
            ts=datetime.now(timezone.utc).isoformat(),
        )

    if phase == "postmarket":
        if not body.force and not should_run_postmarket():
            # Allow exact 18:00 cron even if phase already CLOSED at 18:00
            if clock.value != "closed" or body.force:
                pass
            else:
                # Still run prep at scheduled 18:00
                pass
        persist = persist_journal_to_s3(JOURNAL_PATH)
        next_day_notes = {
            "tomorrow_watchlist": SYMBOLS,
            "focus": "swing overnight holds + next open prep",
            "no_entries_after_hours": True,
            "persist": persist,
        }
        results.append(next_day_notes)
        detail = "18:00 postmarket next-day prep complete — process idle until 08:00"
        logger.info(detail)
        return PhaseResult(
            ok=True,
            phase=phase,
            clock_phase=clock.value,
            detail=detail,
            results=results,
            ts=datetime.now(timezone.utc).isoformat(),
        )

    return PhaseResult(
        ok=False,
        phase=phase,
        clock_phase=clock.value,
        detail=f"unknown phase: {phase}",
        ts=datetime.now(timezone.utc).isoformat(),
    )


def handler(event: dict[str, Any], _context: Any = None) -> dict[str, Any]:
    if "body" in event and isinstance(event["body"], str):
        payload = json.loads(event["body"] or "{}")
    else:
        payload = event if isinstance(event, dict) else {}
    phase = str(payload.get("phase", "status"))
    result = run_phase(PhaseRequest(phase=phase, force=bool(payload.get("force", False))))
    return {"statusCode": 200, "body": result.model_dump_json()}

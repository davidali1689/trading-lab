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

from fastapi import Body, FastAPI, Header, HTTPException, Response
from pydantic import BaseModel, Field

from trading_lab.config.secrets import has_alpaca_keys, load_secrets
from trading_lab.improvement.friday_review import run_friday_review
from trading_lab.improvement.miss_harvest import run_and_persist_miss_harvest
from trading_lab.improvement.postmortem import run_and_persist_postmortem
from trading_lab.journal.grafana_feed import fetch_latest_csv, token_matches
from trading_lab.journal.persist import hydrate_journal_from_s3, persist_journal_to_s3
from trading_lab.observability.cw_emf import emit_tick_metric
from trading_lab.pipeline.eod_flatten import flatten_sniper_paper
from trading_lab.pipeline.paper_agents import run_symbol_paper_tick
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
from trading_lab.selection.watchlist import (
    build_daily_watchlist,
    get_watchlist,
    save_watchlist,
    watchlist_to_csv,
)

logger = logging.getLogger("trading_lab.api")
logging.basicConfig(level=logging.INFO)

# Prefer AWS Secrets Manager (SECRET_ARN) over empty Lambda env for vendor keys.
load_secrets(hydrate=True)

app = FastAPI(title="trading-lab", version="0.2.0")

JOURNAL_PATH = os.environ.get("JOURNAL_PATH", "/tmp/trading-lab-journal.sqlite")  # nosec B108


class PhaseRequest(BaseModel):
    phase: str = Field(
        ...,
        description="premarket | tick | eod | postmarket | weekly_coaches | status",
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
    """Read TRADING_MODE from env each call (tests / Lambda env updates)."""
    raw = os.environ.get("TRADING_MODE", "paper")
    try:
        return RunMode(raw)
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
    wl = get_watchlist()
    return {
        "clock_phase": phase_at().value,
        "sniper_ticks_allowed": sniper_ticks_allowed(),
        "swing_power_hour": swing_power_hour(),
        "entries_enabled": entries_enabled(),
        "process_window": process_window_label(),
        "trading_mode": _mode().value,
        "watchlist": wl.symbols,
        "watchlist_source": wl.source,
        "watchlist_detail": wl.detail,
        "ts": datetime.now(timezone.utc).isoformat(),
    }


def _require_grafana_token(x_grafana_token: str | None) -> None:
    if not token_matches(x_grafana_token):
        raise HTTPException(status_code=401, detail="invalid or missing X-Grafana-Token")


@app.get("/grafana/trades.csv")
def grafana_trades_csv(
    x_grafana_token: str | None = Header(default=None, alias="X-Grafana-Token"),
) -> Response:
    _require_grafana_token(x_grafana_token)
    try:
        body, content_type = fetch_latest_csv("trades")
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return Response(
        content=body,
        media_type=content_type,
        headers={"Cache-Control": "no-store"},
    )


@app.get("/grafana/skips.csv")
def grafana_skips_csv(
    x_grafana_token: str | None = Header(default=None, alias="X-Grafana-Token"),
) -> Response:
    _require_grafana_token(x_grafana_token)
    try:
        body, content_type = fetch_latest_csv("skips")
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return Response(
        content=body,
        media_type=content_type,
        headers={"Cache-Control": "no-store"},
    )


@app.get("/grafana/watchlist.csv")
def grafana_watchlist_csv(
    x_grafana_token: str | None = Header(default=None, alias="X-Grafana-Token"),
) -> Response:
    """Live candidates from S3 watchlist (Infinity datasource)."""
    _require_grafana_token(x_grafana_token)
    try:
        body, content_type = fetch_latest_csv("watchlist")
    except FileNotFoundError:
        # Fallback: build CSV from JSON watchlist if grafana CSV not written yet
        wl = get_watchlist()
        return Response(
            content=watchlist_to_csv(wl).encode("utf-8"),
            media_type="text/csv; charset=utf-8",
            headers={"Cache-Control": "no-store"},
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return Response(
        content=body,
        media_type=content_type,
        headers={"Cache-Control": "no-store"},
    )


@app.get("/grafana/watchlist.json")
def grafana_watchlist_json(
    x_grafana_token: str | None = Header(default=None, alias="X-Grafana-Token"),
) -> dict[str, Any]:
    """JSON array feed for Infinity (more reliable than CSV in table panels)."""
    _require_grafana_token(x_grafana_token)
    wl = get_watchlist()
    rows: list[dict[str, Any]] = []
    if wl.candidates:
        for c in wl.candidates:
            rows.append(
                {
                    "symbol": c.symbol,
                    "status": c.status,
                    "sources": "|".join(c.sources),
                    "price": c.price or "",
                    "volume": c.volume or "",
                    "percent_change": c.percent_change or "",
                    "reason": c.reason,
                    "built_at": wl.built_at,
                    "source": wl.source,
                }
            )
    else:
        for sym in wl.symbols:
            rows.append(
                {
                    "symbol": sym,
                    "status": "CANDIDATE",
                    "sources": "",
                    "price": "",
                    "volume": "",
                    "percent_change": "",
                    "reason": "symbol_only",
                    "built_at": wl.built_at,
                    "source": wl.source,
                }
            )
    return {"count": len(rows), "rows": rows, "detail": wl.detail}


def _emit_from_summary(summary: dict[str, Any]) -> None:
    emit_tick_metric(
        symbol=str(summary.get("symbol", "")),
        status=str(summary.get("status", summary.get("detail", "UNKNOWN"))),
        agent=str(summary.get("found_by_agent", "large_cap_sniper")),
        orders=int(summary.get("orders", 0) or 0),
        skips=int(summary.get("skips", 0) or 0),
    )


@app.post("/run")
def run_phase(body: PhaseRequest) -> PhaseResult:
    return _run_phase(body)


@app.post("/events")
def events(body: dict[str, Any] = Body(default_factory=dict)) -> PhaseResult:
    """EventBridge Scheduler → Lambda Web Adapter posts payload to /events."""
    return _run_phase(
        PhaseRequest(
            phase=str(body.get("phase", "status")),
            force=bool(body.get("force", False)),
            symbol=body.get("symbol"),
        )
    )


def _run_phase(body: PhaseRequest) -> PhaseResult:
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
        wl = build_daily_watchlist()
        persist_wl = save_watchlist(wl)
        detail = (
            f"08:00 prep watchlist={wl.symbols} source={wl.source} "
            f"entries_enabled={entries_enabled()}; no ENTERs until RTH; "
            f"scan={wl.detail}"
        )
        logger.info(detail)
        print(json.dumps({"metric": "premarket_ok", "value": 1}))
        return PhaseResult(
            ok=True,
            phase=phase,
            clock_phase=clock.value,
            detail=detail,
            results=[{"watchlist": wl.to_dict(), "persist": persist_wl}],
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
        if body.symbol:
            symbols = [body.symbol.upper()]
        else:
            wl = get_watchlist()
            symbols = wl.symbols
            if not symbols:
                return PhaseResult(
                    ok=True,
                    phase=phase,
                    clock_phase=clock.value,
                    detail=f"empty watchlist — no-op ({wl.detail or wl.source})",
                    results=[{"watchlist_source": wl.source, "detail": wl.detail}],
                    ts=datetime.now(timezone.utc).isoformat(),
                )
        hydrate = hydrate_journal_from_s3(JOURNAL_PATH)
        power = swing_power_hour()
        for sym in symbols:
            use_mock = os.environ.get("USE_MOCK_BARS", "true").lower() in {
                "1",
                "true",
                "yes",
            }
            # Real paper path: paper mode + keys + live bars (not mock).
            # Budget = current Alpaca equity/5 every tick.
            if _mode() == RunMode.PAPER and has_alpaca_keys() and not use_mock:
                summary = run_symbol_paper_tick(symbol=sym, journal_path=JOURNAL_PATH)
                summary["swing_power_hour"] = power
                summary["budget"] = "platform equity/5, max 3 open (dynamic)"
                _emit_from_summary(summary)
                results.append(summary)
            elif use_mock or _mode() in {RunMode.BACKTEST, RunMode.SIM}:
                # Offline/mock: size from platform equity when keys work; else tests pass equity=.
                summary = run_vertical_slice(
                    symbol=sym,
                    journal_path=JOURNAL_PATH,
                    mode=RunMode.SIM if _mode() == RunMode.PAPER else _mode(),
                )
                summary["swing_power_hour"] = power
                summary["swing_congress"] = evaluate_swing_with_congress(sym, use_mock=use_mock)
                summary["budget"] = "platform equity/5, max 3 open (dynamic)"
                _emit_from_summary(summary)
                results.append(summary)
            else:
                results.append(
                    {
                        "symbol": sym,
                        "swing_power_hour": power,
                        "detail": (
                            "need Alpaca keys + USE_MOCK_BARS=false — "
                            "budget is always current platform equity/5"
                        ),
                    }
                )
        persist = persist_journal_to_s3(JOURNAL_PATH)
        results.append({"hydrate": hydrate, "persist": persist})
        return PhaseResult(
            ok=True,
            phase=phase,
            clock_phase=clock.value,
            detail=(f"tick symbols={len(symbols)} power_hour={power} persist={persist.get('ok')}"),
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
        hydrate = hydrate_journal_from_s3(JOURNAL_PATH)
        flatten = flatten_sniper_paper(JOURNAL_PATH)
        persist = persist_journal_to_s3(JOURNAL_PATH)
        coach = run_and_persist_postmortem(JOURNAL_PATH)
        results.append(
            {"hydrate": hydrate, "flatten": flatten, "persist": persist, "postmortem": coach}
        )
        detail = (
            f"eod flatten + persist + postmortem: flatten={len(flatten)} "
            f"persist={persist.get('ok')} coach={coach.get('ok')} mock={coach.get('mock')}"
        )
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
        hydrate = hydrate_journal_from_s3(JOURNAL_PATH)
        persist = persist_journal_to_s3(JOURNAL_PATH)
        wl = build_daily_watchlist()
        persist_wl = save_watchlist(wl)
        miss = run_and_persist_miss_harvest(JOURNAL_PATH)
        next_day_notes = {
            "tomorrow_watchlist": wl.symbols,
            "watchlist": wl.to_dict(),
            "watchlist_persist": persist_wl,
            "focus": "dynamic candidates for next session — sniper gates at RTH",
            "no_entries_after_hours": True,
            "hydrate": hydrate,
            "persist": persist,
            "miss_harvest": {
                "ok": miss.get("ok"),
                "detail": (miss.get("report") or {}).get("detail"),
                "persist": miss.get("persist"),
            },
        }
        results.append(next_day_notes)
        detail = (
            f"18:00 postmarket prep watchlist={wl.symbols} source={wl.source} "
            f"({wl.detail}) miss_harvest={miss.get('ok')} — idle until 08:00"
        )
        logger.info(detail)
        return PhaseResult(
            ok=True,
            phase=phase,
            clock_phase=clock.value,
            detail=detail,
            results=results,
            ts=datetime.now(timezone.utc).isoformat(),
        )

    if phase == "weekly_coaches":
        # Friday 18:05 — scorecard + four coaches (weekend review pack).
        hydrate = hydrate_journal_from_s3(JOURNAL_PATH)
        pack = run_friday_review(JOURNAL_PATH)
        results.append({"hydrate": hydrate, "friday_review": pack})
        detail = (
            f"friday_review week={pack.get('week_id')} "
            f"scorecard={pack.get('scorecard_summary')} "
            f"coaches_ok={(pack.get('coaches') or {}).get('ok')} "
            f"(pending_green_light)"
        )
        logger.info(detail)
        return PhaseResult(
            ok=bool(pack.get("ok")),
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
    result = _run_phase(PhaseRequest(phase=phase, force=bool(payload.get("force", False))))
    return {"statusCode": 200, "body": result.model_dump_json()}

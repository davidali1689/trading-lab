"""Strategy coaches — one agent_id each. Friday analysis only; no trades."""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, timezone
from typing import Any, Callable

from trading_lab.agents import AGENTS
from trading_lab.improvement.coach_client import CoachClient, coach_model_id
from trading_lab.schemas.misses import CoachProposal, DailyMissReport, MissRecord

logger = logging.getLogger("trading_lab.improvement.coaches")

SYSTEM_TMPL = (
    "You are the {agent_id} improvement coach for trading-lab (paper account). "
    "You analyze missed liquid non-penny gainers attributed to this strategy. "
    "Never recommend placing orders, never override never_force_trade, max positions, "
    "daily loss, budget slices, or sniper EOD flatten. "
    "Output: (1) why the top miss was missed (2) concrete proposed parameter/watchlist "
    "changes as a JSON list under a ```json fence with key proposed_changes "
    "(list of {{path, from, to, rationale}}). Keep analysis under 350 words."
)


def _week_id(ts: datetime | None = None) -> str:
    ts = ts or datetime.now(timezone.utc)
    iso = ts.isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


def _coach_miss_days(default: int = 5) -> int:
    """Trading-week harvest shards each coach reads (default Mon–Fri = 5)."""
    raw = os.environ.get("COACH_MISS_DAYS", str(default))
    try:
        return max(1, min(int(raw), 10))
    except ValueError:
        return default


def _load_week_misses_from_s3(
    *,
    bucket: str | None = None,
    agent_id: str,
    max_days: int | None = None,
) -> list[dict[str, Any]]:
    bucket = bucket or os.environ.get("JOURNAL_S3_BUCKET", "")
    max_days = _coach_miss_days() if max_days is None else max_days
    if not bucket:
        return []
    try:
        import boto3
    except ImportError:
        return []
    client = boto3.client("s3")
    # List recent day prefixes under misses/
    try:
        resp = client.list_objects_v2(Bucket=bucket, Prefix="misses/", Delimiter="/")
    except Exception as exc:  # noqa: BLE001
        logger.warning("list misses failed: %s", exc)
        return []
    days = []
    for p in resp.get("CommonPrefixes") or []:
        prefix = p.get("Prefix") or ""
        # misses/2026-07-18/
        parts = prefix.strip("/").split("/")
        if len(parts) >= 2 and re.match(r"\d{4}-\d{2}-\d{2}", parts[1]):
            days.append(parts[1])
    days = sorted(days, reverse=True)[:max_days]
    out: list[dict[str, Any]] = []
    for day in days:
        key = f"misses/{day}/by_agent/{agent_id}.json"
        try:
            obj = client.get_object(Bucket=bucket, Key=key)
            out.append(json.loads(obj["Body"].read().decode()))
        except Exception:  # noqa: BLE001
            continue
    return out


def _parse_proposed_changes(text: str) -> list[dict[str, Any]]:
    fence = re.search(r"```json\s*(\{.*?\}|\[.*?\])\s*```", text, re.DOTALL)
    raw = fence.group(1) if fence else None
    if not raw:
        # try whole-text JSON object
        try:
            data = json.loads(text)
            if isinstance(data, dict) and "proposed_changes" in data:
                ch = data["proposed_changes"]
                return ch if isinstance(ch, list) else []
            if isinstance(data, list):
                return data
        except json.JSONDecodeError:
            return []
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if isinstance(data, dict):
        ch = data.get("proposed_changes", data)
        return ch if isinstance(ch, list) else []
    if isinstance(data, list):
        return data
    return []


def run_strategy_coach(
    agent_id: str,
    *,
    report: DailyMissReport | None = None,
    client: CoachClient | None = None,
    week_misses: list[dict[str, Any]] | None = None,
    scorecard: dict[str, Any] | None = None,
) -> CoachProposal:
    if agent_id not in AGENTS:
        raise KeyError(f"unknown agent_id={agent_id}")
    client = client or CoachClient()
    week_id = _week_id()
    built_at = datetime.now(timezone.utc).isoformat()

    top_miss: MissRecord | None = None
    if report and report.per_agent_top_miss:
        top_miss = report.per_agent_top_miss.get(agent_id)

    corpus = week_misses
    if corpus is None:
        corpus = _load_week_misses_from_s3(agent_id=agent_id)

    grounding = []
    if top_miss:
        grounding.append(top_miss.symbol)
    for shard in corpus:
        tm = shard.get("top_miss") or {}
        if tm.get("symbol"):
            grounding.append(str(tm["symbol"]))
        for rel in shard.get("related") or []:
            if rel.get("symbol"):
                grounding.append(str(rel["symbol"]))
    grounding = sorted(set(grounding))

    agent_score = None
    if scorecard:
        agents = scorecard.get("agents") or {}
        agent_score = agents.get(agent_id)
    payload = {
        "agent_id": agent_id,
        "week_id": week_id,
        "latest_top_miss": top_miss.model_dump(mode="json") if top_miss else None,
        "week_shards": corpus,
        "scorecard_for_agent": agent_score,
        "scorecard_summary": (scorecard or {}).get("summary"),
        "immutable_guardrails": [
            "never_force_trade",
            "max_positions",
            "daily_loss",
            "budget_slices",
            "sniper_eod_flatten",
            "paper_only",
        ],
    }
    system = SYSTEM_TMPL.format(agent_id=agent_id)
    analysis = client.analyze(system, json.dumps(payload, indent=2))
    changes = _parse_proposed_changes(analysis)
    return CoachProposal(
        week_id=week_id,
        agent_id=agent_id,
        built_at=built_at,
        model_id=client.model_id,
        top_miss=top_miss,
        analysis=analysis,
        proposed_changes=changes,
        status="pending_green_light",
        grounding_symbols=grounding,
        mock=client.mock,
    )


COACH_RUNNERS: dict[str, Callable[..., CoachProposal]] = {
    aid: (lambda aid=aid, **kw: run_strategy_coach(aid, **kw)) for aid in AGENTS
}


def persist_proposal(proposal: CoachProposal, *, bucket: str | None = None) -> dict[str, Any]:
    bucket = bucket or os.environ.get("JOURNAL_S3_BUCKET", "")
    body = json.dumps(proposal.to_dict(), indent=2)
    if not bucket:
        return {"ok": False, "detail": "JOURNAL_S3_BUCKET unset", "proposal": proposal.to_dict()}
    try:
        import boto3
    except ImportError:
        return {"ok": False, "detail": "boto3 not installed"}
    client = boto3.client("s3")
    keys = [
        f"proposals/{proposal.week_id}/{proposal.agent_id}.json",
        f"proposals/latest/{proposal.agent_id}.json",
    ]
    for key in keys:
        client.put_object(
            Bucket=bucket,
            Key=key,
            Body=body.encode("utf-8"),
            ContentType="application/json",
        )
    return {"ok": True, "bucket": bucket, "keys": keys}


def run_weekly_coaches(
    *,
    report: DailyMissReport | None = None,
    client: CoachClient | None = None,
    scorecard: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run each registered strategy coach separately; persist each proposal. No overlay apply."""
    results: list[dict[str, Any]] = []
    for agent_id in AGENTS:
        try:
            prop = run_strategy_coach(
                agent_id,
                report=report,
                client=client,
                scorecard=scorecard,
            )
            persist = persist_proposal(prop)
            results.append(
                {
                    "agent_id": agent_id,
                    "ok": True,
                    "persist": persist,
                    "status": prop.status,
                    "mock": prop.mock,
                    "top_miss": prop.top_miss.symbol if prop.top_miss else None,
                }
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("coach failed agent_id=%s", agent_id)
            results.append({"agent_id": agent_id, "ok": False, "detail": str(exc)})
    ok = all(r.get("ok") for r in results)
    return {
        "ok": ok,
        "week_id": _week_id(),
        "model_id": coach_model_id(),
        "coaches": results,
        "note": "proposals pending_green_light — do not auto-apply",
    }

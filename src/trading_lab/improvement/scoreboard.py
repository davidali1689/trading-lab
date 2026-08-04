"""Daily ops scoreboard + Grafana feed (daily + weekly rows)."""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from trading_lab.agents import AGENTS
from trading_lab.improvement.scorecard import (
    _journal_window,
    _max_drawdown,
    week_id_for,
)
from trading_lab.schemas.scorecard import AgentScorecard, DailyScoreboard, WeeklyScorecard

logger = logging.getLogger("trading_lab.improvement.scoreboard")

OPS_ROW_FIELDS = (
    "agent_id",
    "trade_count",
    "skip_count",
    "win_count",
    "loss_count",
    "win_rate",
    "loss_rate",
    "net_pnl_usd",
    "expectancy_usd",
    "max_drawdown_usd",
)


def _day_bounds(day: str) -> tuple[datetime, datetime]:
    start = datetime.fromisoformat(day).replace(tzinfo=timezone.utc)
    return start, start + timedelta(days=1)


def _ops_stats_from_pnls(
    series: list[Decimal],
    *,
    skip_count: int,
    agent_id: str,
) -> AgentScorecard:
    """Build an AgentScorecard with ops metrics (improvement fields left default)."""
    n = len(series)
    net = sum(series, Decimal("0"))
    exp = (net / Decimal(n)) if n else Decimal("0")
    wins = sum(1 for p in series if p > 0)
    losses = (n - wins) if n else 0  # break-even counts as loss
    win_rate = (Decimal(wins) / Decimal(n)) if n else Decimal("0")
    loss_rate = (Decimal(losses) / Decimal(n)) if n else Decimal("0")
    max_dd = _max_drawdown(series)
    return AgentScorecard(
        agent_id=agent_id,
        trade_count=n,
        skip_count=skip_count,
        win_count=wins,
        loss_count=losses,
        expectancy_usd=str(exp.quantize(Decimal("0.01"))),
        win_rate=str(win_rate.quantize(Decimal("0.01"))),
        loss_rate=str(loss_rate.quantize(Decimal("0.01"))),
        net_pnl_usd=str(net.quantize(Decimal("0.01"))),
        max_drawdown_usd=str(max_dd.quantize(Decimal("0.01"))),
        detail=f"trades={n} skips={skip_count}",
    )


def build_daily_scoreboard(
    journal_path: str | Path,
    *,
    day: str | None = None,
) -> DailyScoreboard:
    day = day or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    built_at = datetime.now(timezone.utc).isoformat()
    start, end = _day_bounds(day)
    pnls, skips = _journal_window(journal_path, start=start, end=end)
    agents: dict[str, AgentScorecard] = {}
    for agent_id in AGENTS:
        agents[agent_id] = _ops_stats_from_pnls(
            pnls.get(agent_id, []),
            skip_count=skips.get(agent_id, 0),
            agent_id=agent_id,
        )
    summary = "; ".join(
        f"{aid}:t={a.trade_count}/s={a.skip_count}/wr={a.win_rate}" for aid, a in agents.items()
    )
    return DailyScoreboard(day=day, built_at=built_at, agents=agents, summary=summary)


def _agent_to_ops_row(card: AgentScorecard) -> dict[str, Any]:
    data = card.model_dump(mode="json")
    return {k: data[k] for k in OPS_ROW_FIELDS}


def _zero_ops_row(agent_id: str) -> dict[str, Any]:
    return _agent_to_ops_row(AgentScorecard(agent_id=agent_id, win_rate="0.00", loss_rate="0.00"))


def _empty_agent_rows() -> list[dict[str, Any]]:
    return [_zero_ops_row(aid) for aid in AGENTS]


def empty_scoreboard_feed() -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    rows = _empty_agent_rows()
    return {
        "built_at": now.isoformat(),
        "daily": {"period_id": now.strftime("%Y-%m-%d"), "rows": rows},
        "weekly": {"period_id": week_id_for(now), "rows": [dict(r) for r in rows]},
    }


def build_scoreboard_feed(
    *,
    daily: DailyScoreboard | None = None,
    weekly: WeeklyScorecard | None = None,
) -> dict[str, Any]:
    built_at = datetime.now(timezone.utc).isoformat()
    if daily is None:
        daily_block = {
            "period_id": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            "rows": _empty_agent_rows(),
        }
    else:
        daily_block = {
            "period_id": daily.day,
            "rows": [_agent_to_ops_row(daily.agents[aid]) for aid in AGENTS if aid in daily.agents]
            or _empty_agent_rows(),
        }
        # Ensure all agents present
        have = {r["agent_id"] for r in daily_block["rows"]}
        for aid in AGENTS:
            if aid not in have:
                daily_block["rows"].append(_zero_ops_row(aid))

    if weekly is None:
        weekly_block = {
            "period_id": week_id_for(),
            "rows": _empty_agent_rows(),
        }
    else:
        weekly_block = {
            "period_id": weekly.week_id,
            "rows": [
                _agent_to_ops_row(weekly.agents[aid]) for aid in AGENTS if aid in weekly.agents
            ],
        }
        have = {r["agent_id"] for r in weekly_block["rows"]}
        for aid in AGENTS:
            if aid not in have:
                weekly_block["rows"].append(_zero_ops_row(aid))

    return {"built_at": built_at, "daily": daily_block, "weekly": weekly_block}


def _put_json(client: Any, bucket: str, key: str, body: dict[str, Any]) -> None:
    client.put_object(
        Bucket=bucket,
        Key=key,
        Body=json.dumps(body, indent=2).encode("utf-8"),
        ContentType="application/json",
    )


def _load_json(client: Any, bucket: str, key: str) -> dict[str, Any] | None:
    try:
        obj = client.get_object(Bucket=bucket, Key=key)
        data = json.loads(obj["Body"].read().decode("utf-8"))
        return data if isinstance(data, dict) else None
    except Exception:  # noqa: BLE001
        return None


def _persist_scoreboard_feed(
    feed: dict[str, Any],
    *,
    bucket: str | None = None,
) -> dict[str, Any]:
    bucket = bucket or os.environ.get("JOURNAL_S3_BUCKET", "")
    if not bucket:
        return {"ok": False, "detail": "JOURNAL_S3_BUCKET unset", "feed": feed}
    try:
        import boto3
    except ImportError:
        return {"ok": False, "detail": "boto3 not installed"}
    key = "grafana/latest/scoreboard.json"
    client = boto3.client("s3")
    _put_json(client, bucket, key, feed)
    logger.info("persisted scoreboard feed s3://%s/%s", bucket, key)
    return {"ok": True, "bucket": bucket, "keys": [key]}


def merge_and_persist_scoreboard_feed(
    *,
    daily: DailyScoreboard | None = None,
    weekly: WeeklyScorecard | None = None,
    bucket: str | None = None,
) -> dict[str, Any]:
    """Refresh Grafana feed, preserving the other period from S3 when only one is new."""
    bucket = bucket or os.environ.get("JOURNAL_S3_BUCKET", "")
    existing: dict[str, Any] | None = None
    if bucket:
        try:
            import boto3

            existing = _load_json(boto3.client("s3"), bucket, "grafana/latest/scoreboard.json")
        except Exception:  # noqa: BLE001
            existing = None

    feed = build_scoreboard_feed(daily=daily, weekly=weekly)
    if existing:
        if daily is None and isinstance(existing.get("daily"), dict):
            feed["daily"] = existing["daily"]
        if weekly is None and isinstance(existing.get("weekly"), dict):
            feed["weekly"] = existing["weekly"]
    return _persist_scoreboard_feed(feed, bucket=bucket)


def persist_daily_scoreboard(
    board: DailyScoreboard,
    *,
    bucket: str | None = None,
    weekly: WeeklyScorecard | None = None,
) -> dict[str, Any]:
    bucket = bucket or os.environ.get("JOURNAL_S3_BUCKET", "")
    body = board.to_dict()
    if not bucket:
        return {"ok": False, "detail": "JOURNAL_S3_BUCKET unset", "scoreboard": body}
    try:
        import boto3
    except ImportError:
        return {"ok": False, "detail": "boto3 not installed"}
    client = boto3.client("s3")
    keys = [
        f"scoreboards/daily/{board.day}.json",
        "scoreboards/daily/latest.json",
    ]
    for key in keys:
        _put_json(client, bucket, key, body)

    # Prefer loading latest weekly from S3 when not passed
    if weekly is None:
        raw = _load_json(client, bucket, "scorecards/latest.json")
        if raw:
            try:
                weekly = WeeklyScorecard.model_validate(raw)
            except Exception:  # noqa: BLE001
                weekly = None

    feed_out = merge_and_persist_scoreboard_feed(daily=board, weekly=weekly, bucket=bucket)
    keys.extend(feed_out.get("keys") or [])
    logger.info("persisted daily scoreboard %s", board.day)
    return {
        "ok": bool(feed_out.get("ok")),
        "bucket": bucket,
        "keys": keys,
        "summary": board.summary,
        "feed": feed_out,
    }


def run_and_persist_daily_scoreboard(
    journal_path: str | Path,
    *,
    day: str | None = None,
) -> dict[str, Any]:
    board = build_daily_scoreboard(journal_path, day=day)
    persist = persist_daily_scoreboard(board)
    return {
        "ok": persist.get("ok", False),
        "scoreboard": board.to_dict(),
        "persist": persist,
    }


def refresh_weekly_scoreboard_feed(
    weekly: WeeklyScorecard,
    *,
    bucket: str | None = None,
) -> dict[str, Any]:
    """After Friday scorecard persist — update weekly half of Grafana feed."""
    return merge_and_persist_scoreboard_feed(daily=None, weekly=weekly, bucket=bucket)

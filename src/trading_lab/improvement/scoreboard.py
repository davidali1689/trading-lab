"""Daily ops scoreboard (S3 scoreboards/daily + terminal)."""

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
)
from trading_lab.schemas.scorecard import AgentScorecard, DailyScoreboard

logger = logging.getLogger("trading_lab.improvement.scoreboard")


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


def _put_json(client: Any, bucket: str, key: str, body: dict[str, Any]) -> None:
    client.put_object(
        Bucket=bucket,
        Key=key,
        Body=json.dumps(body, indent=2).encode("utf-8"),
        ContentType="application/json",
    )


def persist_daily_scoreboard(
    board: DailyScoreboard,
    *,
    bucket: str | None = None,
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

    logger.info("persisted daily scoreboard %s", board.day)
    return {
        "ok": True,
        "bucket": bucket,
        "keys": keys,
        "summary": board.summary,
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

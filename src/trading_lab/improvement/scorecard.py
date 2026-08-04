"""Friday weekly scorecard — deterministic better/worse vs prior week. No orders."""

from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from trading_lab.agents import AGENTS
from trading_lab.schemas.scorecard import AgentScorecard, Trend, WeeklyScorecard

logger = logging.getLogger("trading_lab.improvement.scorecard")

DEFAULT_DD_CAP = Decimal("500")
COMPOSITE_EPS = Decimal("1")  # min delta to call improving/worse


def week_id_for(ts: datetime | None = None) -> str:
    ts = ts or datetime.now(timezone.utc)
    iso = ts.isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


def prior_week_id(week_id: str) -> str:
    m = re.match(r"(\d{4})-W(\d{2})", week_id)
    if not m:
        return week_id
    year, week = int(m.group(1)), int(m.group(2))
    # Monday of that ISO week → minus 7 days
    monday = datetime.fromisocalendar(year, week, 1).replace(tzinfo=timezone.utc)
    prev = monday - timedelta(days=7)
    iso = prev.isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


def _week_bounds(week_id: str) -> tuple[datetime, datetime]:
    m = re.match(r"(\d{4})-W(\d{2})", week_id)
    if not m:
        now = datetime.now(timezone.utc)
        start = now - timedelta(days=7)
        return start, now
    year, week = int(m.group(1)), int(m.group(2))
    start = datetime.fromisocalendar(year, week, 1).replace(tzinfo=timezone.utc)
    end = start + timedelta(days=7)
    return start, end


def _parse_ts(raw: str) -> datetime | None:
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:  # noqa: BLE001
        return None


def _journal_window(
    db_path: str | Path,
    *,
    start: datetime,
    end: datetime,
) -> tuple[dict[str, list[Decimal]], dict[str, int]]:
    """Per-agent pnl list and skip counts in [start, end)."""
    db_path = Path(db_path)
    pnls: dict[str, list[Decimal]] = {aid: [] for aid in AGENTS}
    skips: dict[str, int] = {aid: 0 for aid in AGENTS}
    if not db_path.exists():
        return pnls, skips
    with sqlite3.connect(db_path) as conn:
        for row in conn.execute("SELECT found_by_agent, pnl_usd, entry_ts FROM trades"):
            agent, pnl_s, ts_s = str(row[0]), str(row[1] or "0"), str(row[2])
            ts = _parse_ts(ts_s)
            if ts is None or not (start <= ts < end):
                continue
            if agent not in pnls:
                pnls[agent] = []
            try:
                pnls[agent].append(Decimal(pnl_s))
            except Exception:  # noqa: BLE001
                pnls[agent].append(Decimal("0"))
        for row in conn.execute("SELECT found_by_agent, ts FROM skips"):
            agent, ts_s = str(row[0]), str(row[1])
            ts = _parse_ts(ts_s)
            if ts is None or not (start <= ts < end):
                continue
            skips[agent] = skips.get(agent, 0) + 1
    return pnls, skips


def _max_drawdown(pnls: list[Decimal]) -> Decimal:
    if not pnls:
        return Decimal("0")
    equity = Decimal("0")
    peak = Decimal("0")
    max_dd = Decimal("0")
    for p in pnls:
        equity += p
        if equity > peak:
            peak = equity
        dd = peak - equity
        if dd > max_dd:
            max_dd = dd
    return max_dd


def _capture_from_miss_shards(
    shards: list[dict[str, Any]],
    agent_id: str,
) -> tuple[int, int]:
    """opportunities, captured from miss harvest by_agent shards."""
    opps = 0
    captured = 0
    for shard in shards:
        if shard.get("agent_id") and shard["agent_id"] != agent_id:
            continue
        related = shard.get("related") or []
        if not related and shard.get("top_miss"):
            related = [shard["top_miss"]]
        for row in related:
            if not isinstance(row, dict) or not row.get("symbol"):
                continue
            # swing sees all; snipers only their owner band
            owner = str(row.get("owner_sniper") or "")
            if agent_id != "swing_momentum" and owner and owner != agent_id:
                continue
            opps += 1
            traded_by = row.get("traded_by") or []
            bucket = str(row.get("bucket") or "")
            # Captured = agent participated with positive P&L. Previously bucket C
            # (entered, weak vs the move) never counted, pinning capture_rate at 0.
            if agent_id in traded_by:
                pnl_pct = row.get("trade_pnl_pct")
                try:
                    if pnl_pct is not None and Decimal(str(pnl_pct)) > 0:
                        captured += 1
                except Exception:  # noqa: BLE001
                    pass
            elif bucket == "" and row.get("trade_pnl_pct"):
                try:
                    if Decimal(str(row["trade_pnl_pct"])) > 0:
                        captured += 1
                except Exception:  # noqa: BLE001
                    pass
    return opps, captured


def _load_miss_shards_for_week(
    week_id: str,
    *,
    bucket: str | None = None,
    injected: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    if injected is not None:
        return injected
    bucket = bucket or os.environ.get("JOURNAL_S3_BUCKET", "")
    if not bucket:
        return []
    start, end = _week_bounds(week_id)
    try:
        import boto3
    except ImportError:
        return []
    client = boto3.client("s3")
    out: list[dict[str, Any]] = []
    day = start.date()
    while day < end.date():
        day_s = day.isoformat()
        for agent_id in AGENTS:
            key = f"misses/{day_s}/by_agent/{agent_id}.json"
            try:
                obj = client.get_object(Bucket=bucket, Key=key)
                shard = json.loads(obj["Body"].read().decode())
                shard.setdefault("agent_id", agent_id)
                out.append(shard)
            except Exception:  # noqa: BLE001
                continue
        day += timedelta(days=1)
    return out


def _load_prior_scorecard(
    week_id: str,
    *,
    bucket: str | None = None,
    injected: WeeklyScorecard | None = None,
) -> WeeklyScorecard | None:
    if injected is not None:
        return injected
    bucket = bucket or os.environ.get("JOURNAL_S3_BUCKET", "")
    if not bucket:
        return None
    try:
        import boto3
    except ImportError:
        return None
    key = f"scorecards/{week_id}.json"
    try:
        obj = boto3.client("s3").get_object(Bucket=bucket, Key=key)
        return WeeklyScorecard.model_validate(json.loads(obj["Body"].read().decode()))
    except Exception:  # noqa: BLE001
        return None


def _composite(
    *,
    capture_rate: Decimal,
    expectancy: Decimal,
    max_dd: Decimal,
    dd_cap: Decimal,
) -> Decimal:
    # capture in [0,50], expectancy clipped ±50, DD over cap penalizes
    cap_part = capture_rate * Decimal("50")
    exp_part = max(Decimal("-50"), min(Decimal("50"), expectancy))
    over = max(Decimal("0"), max_dd - dd_cap)
    return cap_part + exp_part - over * Decimal("0.1")


def build_weekly_scorecard(
    journal_path: str | Path,
    *,
    week_id: str | None = None,
    miss_shards: list[dict[str, Any]] | None = None,
    prior: WeeklyScorecard | None = None,
    drawdown_cap_usd: Decimal | None = None,
) -> WeeklyScorecard:
    week_id = week_id or week_id_for()
    prev_id = prior_week_id(week_id)
    built_at = datetime.now(timezone.utc).isoformat()
    dd_cap = drawdown_cap_usd or Decimal(
        os.environ.get("SCORECARD_DD_CAP_USD", str(DEFAULT_DD_CAP))
    )
    start, end = _week_bounds(week_id)
    pnls, skips = _journal_window(journal_path, start=start, end=end)
    shards = _load_miss_shards_for_week(week_id, injected=miss_shards)
    prior_card = prior if prior is not None else _load_prior_scorecard(prev_id)

    agents: dict[str, AgentScorecard] = {}
    worse_agents: list[str] = []
    for agent_id in AGENTS:
        series = pnls.get(agent_id, [])
        n = len(series)
        net = sum(series, Decimal("0"))
        exp = (net / Decimal(n)) if n else Decimal("0")
        wins = sum(1 for p in series if p > 0)
        losses = (n - wins) if n else 0
        win_rate = (Decimal(wins) / Decimal(n)) if n else Decimal("0")
        loss_rate = (Decimal(losses) / Decimal(n)) if n else Decimal("0")
        max_dd = _max_drawdown(series)
        agent_shards = [s for s in shards if s.get("agent_id") == agent_id]
        opps, captured = _capture_from_miss_shards(
            agent_shards if agent_shards else shards,
            agent_id,
        )
        cap_rate = (Decimal(captured) / Decimal(opps)) if opps else Decimal("0")
        comp = _composite(
            capture_rate=cap_rate,
            expectancy=exp,
            max_dd=max_dd,
            dd_cap=dd_cap,
        )
        trend = Trend.INSUFFICIENT_DATA
        propose_revert = False
        detail = f"trades={n} opps={opps}"
        if prior_card and agent_id in prior_card.agents:
            try:
                prev_c = Decimal(prior_card.agents[agent_id].composite)
                if n == 0 and opps == 0:
                    trend = Trend.INSUFFICIENT_DATA
                elif comp > prev_c + COMPOSITE_EPS:
                    trend = Trend.IMPROVING
                elif comp < prev_c - COMPOSITE_EPS:
                    trend = Trend.WORSE
                    propose_revert = True
                    worse_agents.append(agent_id)
                else:
                    trend = Trend.FLAT
                detail += f" vs_prior_composite={prev_c}"
            except Exception:  # noqa: BLE001
                trend = Trend.INSUFFICIENT_DATA
        elif n > 0 or opps > 0:
            trend = Trend.FLAT
            detail += " no_prior_scorecard"

        agents[agent_id] = AgentScorecard(
            agent_id=agent_id,
            trade_count=n,
            skip_count=skips.get(agent_id, 0),
            win_count=wins,
            loss_count=losses,
            expectancy_usd=str(exp.quantize(Decimal("0.01"))),
            win_rate=str(win_rate.quantize(Decimal("0.01"))),
            loss_rate=str(loss_rate.quantize(Decimal("0.01"))),
            net_pnl_usd=str(net.quantize(Decimal("0.01"))),
            max_drawdown_usd=str(max_dd.quantize(Decimal("0.01"))),
            capture_rate=str(cap_rate.quantize(Decimal("0.01"))),
            gainer_opportunities=opps,
            gainers_captured=captured,
            composite=str(comp.quantize(Decimal("0.01"))),
            trend=trend,
            propose_revert=propose_revert,
            detail=detail,
        )

    summary_parts = [f"{aid}:{a.trend.value}" for aid, a in agents.items()]
    if worse_agents:
        summary_parts.append(f"propose_revert_flag={','.join(worse_agents)}")
    return WeeklyScorecard(
        week_id=week_id,
        built_at=built_at,
        prior_week_id=prev_id,
        drawdown_cap_usd=str(dd_cap),
        agents=agents,
        summary="; ".join(summary_parts),
    )


def persist_scorecard(
    card: WeeklyScorecard,
    *,
    bucket: str | None = None,
) -> dict[str, Any]:
    bucket = bucket or os.environ.get("JOURNAL_S3_BUCKET", "")
    body = json.dumps(card.to_dict(), indent=2)
    if not bucket:
        return {"ok": False, "detail": "JOURNAL_S3_BUCKET unset", "scorecard": card.to_dict()}
    try:
        import boto3
    except ImportError:
        return {"ok": False, "detail": "boto3 not installed"}
    client = boto3.client("s3")
    keys = [
        f"scorecards/{card.week_id}.json",
        "scorecards/latest.json",
        f"proposals/{card.week_id}/_scorecard.json",
    ]
    for key in keys:
        client.put_object(
            Bucket=bucket,
            Key=key,
            Body=body.encode("utf-8"),
            ContentType="application/json",
        )
    try:
        from trading_lab.improvement.scoreboard import refresh_weekly_scoreboard_feed

        feed_out = refresh_weekly_scoreboard_feed(card, bucket=bucket)
        if feed_out.get("keys"):
            keys.extend(feed_out["keys"])
    except Exception:  # noqa: BLE001
        logger.exception("scoreboard feed refresh failed for %s", card.week_id)
    logger.info("persisted scorecard %s", card.week_id)
    return {"ok": True, "bucket": bucket, "keys": keys, "summary": card.summary}



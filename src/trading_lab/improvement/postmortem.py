"""EOD post-mortem: deterministic journal digest + optional Bedrock narrative.

Never used on tick/entry paths — coach / ops only.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from trading_lab.improvement.bedrock_client import BedrockClient, mock_bedrock_enabled

logger = logging.getLogger("trading_lab.improvement.postmortem")

SYSTEM_PROMPT = (
    "You are a trading-lab post-mortem coach. Summarize the journal digest for operators. "
    "Do not recommend placing orders, changing stops/targets, or overriding risk gates. "
    "Focus on skip-reason patterns, trade counts, and P&L totals. Keep it under 200 words."
)


def digest_journal(db_path: str | Path) -> dict[str, Any]:
    """Aggregate skips/trades from sqlite (Grafana CSV source of truth)."""
    db_path = Path(db_path)
    skips_by_reason: Counter[str] = Counter()
    skips_by_agent: Counter[str] = Counter()
    trades_by_agent: Counter[str] = Counter()
    symbols_traded: list[str] = []
    pnl_total = Decimal("0")
    trade_count = 0
    skip_count = 0

    if not db_path.exists():
        return {
            "trade_count": 0,
            "skip_count": 0,
            "skips_by_reason": {},
            "skips_by_agent": {},
            "trades_by_agent": {},
            "symbols_traded": [],
            "pnl_usd_total": "0.00",
            "detail": "missing journal file",
        }

    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        for row in conn.execute("SELECT skip_reason, found_by_agent FROM skips"):
            skip_count += 1
            skips_by_reason[str(row["skip_reason"])] += 1
            skips_by_agent[str(row["found_by_agent"])] += 1
        for row in conn.execute(
            "SELECT found_by_agent, symbol, pnl_usd FROM trades ORDER BY entry_ts"
        ):
            trade_count += 1
            agent = str(row["found_by_agent"])
            trades_by_agent[agent] += 1
            sym = str(row["symbol"])
            if sym not in symbols_traded:
                symbols_traded.append(sym)
            try:
                pnl_total += Decimal(str(row["pnl_usd"] or "0"))
            except Exception:  # noqa: BLE001
                pass

    return {
        "trade_count": trade_count,
        "skip_count": skip_count,
        "skips_by_reason": dict(skips_by_reason),
        "skips_by_agent": dict(skips_by_agent),
        "trades_by_agent": dict(trades_by_agent),
        "symbols_traded": symbols_traded,
        "pnl_usd_total": f"{pnl_total.quantize(Decimal('0.01'))}",
    }


def run_postmortem(
    db_path: str | Path,
    *,
    client: BedrockClient | None = None,
) -> dict[str, Any]:
    """Build digest + Bedrock (or mock) narrative."""
    digest = digest_journal(db_path)
    bedrock = client or BedrockClient()
    user_msg = json.dumps(digest, sort_keys=True)
    try:
        narrative = bedrock.converse(SYSTEM_PROMPT, user_msg)
    except Exception as exc:  # noqa: BLE001
        logger.exception("bedrock converse failed")
        return {
            "ok": False,
            "digest": digest,
            "narrative": "",
            "mock": bedrock.mock,
            "model_id": bedrock.model_id,
            "detail": f"bedrock failed: {exc}",
            "ts": datetime.now(timezone.utc).isoformat(),
        }
    return {
        "ok": True,
        "digest": digest,
        "narrative": narrative,
        "mock": bedrock.mock if client is None else bedrock.mock,
        "model_id": bedrock.model_id,
        "ts": datetime.now(timezone.utc).isoformat(),
    }


def persist_postmortem(
    report: dict[str, Any],
    *,
    bucket: str | None = None,
    prefix: str = "journals",
    day: str | None = None,
) -> dict[str, Any]:
    """Upload postmortem.json (dated)."""
    bucket = bucket or os.environ.get("JOURNAL_S3_BUCKET", "")
    if not bucket:
        return {"ok": False, "detail": "JOURNAL_S3_BUCKET unset — skip postmortem persist"}

    try:
        import boto3
    except ImportError:
        return {"ok": False, "detail": "boto3 not installed"}

    day = day or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    body = json.dumps(report, indent=2).encode("utf-8")
    dated = f"{prefix.rstrip('/')}/{day}/postmortem.json"
    client = boto3.client("s3")
    client.put_object(Bucket=bucket, Key=dated, Body=body, ContentType="application/json")
    logger.info("persisted postmortem to s3://%s/%s", bucket, dated)
    return {"ok": True, "bucket": bucket, "keys": [dated]}


def run_and_persist_postmortem(db_path: str | Path) -> dict[str, Any]:
    """EOD helper: digest + narrative + S3 upload."""
    report = run_postmortem(db_path)
    persist = persist_postmortem(report)
    report["persist"] = persist
    report["mock_bedrock"] = mock_bedrock_enabled()
    return report

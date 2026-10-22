"""Premarket findings written for the paper worker to read.

Cursor research uploads JSON. The tick loads it. A high risk score blocks
the order. A catalyst score can satisfy the news check. The risk gate and
the bracket order stay on the existing path.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

from trading_lab.schedule.market_clock import now_et

logger = logging.getLogger("trading_lab.journal.findings")


FINDINGS_ROOT = "findings"


def premarket_finding_key(day: str, symbol: str) -> str:
    """Same key the morning researcher uploads."""
    return f"{FINDINGS_ROOT}/premarket/{day}/{symbol.upper()}.json"


def proposal_finding_prefix(week: str) -> str:
    return f"{FINDINGS_ROOT}/proposals/{week}/"


def replay_finding_prefix(week: str) -> str:
    return f"{FINDINGS_ROOT}/replays/{week}/"


def proposal_finding_key(week: str, agent_id: str) -> str:
    """Same key the Friday miss researcher uploads."""
    return f"{proposal_finding_prefix(week)}{agent_id}.json"


def replay_finding_key(week: str, agent_id: str) -> str:
    """Same key the replay researcher uploads."""
    return f"{replay_finding_prefix(week)}{agent_id}.json"


def _score(payload: dict[str, Any] | None, key: str) -> int:
    if not payload:
        return 0
    try:
        return int(payload.get(key) or 0)
    except (TypeError, ValueError):
        return 0


def finding_blocks_trade(payload: dict[str, Any] | None) -> bool:
    """Risk 2 means do not send an order for this name today."""
    return _score(payload, "risk_score") >= 2


def finding_has_catalyst(payload: dict[str, Any] | None) -> bool:
    """Catalyst 1 or 2 counts as the news check on the buy checklist."""
    return _score(payload, "catalyst_score") >= 1


def upload_premarket_finding(
    payload: dict[str, Any],
    *,
    bucket: str | None = None,
    client: Any | None = None,
) -> dict[str, Any]:
    """Put one finding in the journal bucket. No order is sent."""
    bucket = bucket or os.environ.get("JOURNAL_S3_BUCKET", "")
    if not bucket:
        return {"ok": False, "detail": "JOURNAL_S3_BUCKET unset"}
    symbol = str(payload.get("symbol") or "").upper()
    day = str(payload.get("day") or now_et().date().isoformat())
    if not symbol:
        return {"ok": False, "detail": "symbol required"}
    body = json.dumps({**payload, "symbol": symbol, "day": day}).encode("utf-8")
    key = premarket_finding_key(day, symbol)
    s3 = client or _client()
    if s3 is None:
        return {"ok": False, "detail": "boto3 not installed"}
    s3.put_object(Bucket=bucket, Key=key, Body=body, ContentType="application/json")
    logger.info("uploaded finding s3://%s/%s", bucket, key)
    return {"ok": True, "bucket": bucket, "key": key}


def load_premarket_finding(
    symbol: str,
    *,
    day: str | None = None,
    bucket: str | None = None,
    client: Any | None = None,
) -> dict[str, Any] | None:
    """Load today's finding. Missing file or missing bucket means no finding."""
    bucket = bucket or os.environ.get("JOURNAL_S3_BUCKET", "")
    if not bucket:
        return None
    day = day or now_et().date().isoformat()
    key = premarket_finding_key(day, symbol)
    s3 = client or _client()
    if s3 is None:
        return None
    try:
        obj = s3.get_object(Bucket=bucket, Key=key)
    except Exception:  # noqa: BLE001 — no finding is a normal tick
        return None
    raw = obj["Body"].read()
    if isinstance(raw, str):
        text = raw
    else:
        text = raw.decode("utf-8")
    data = json.loads(text)
    if not isinstance(data, dict):
        return None
    return data


def list_finding_keys(
    prefix: str,
    *,
    bucket: str | None = None,
    client: Any | None = None,
) -> list[str]:
    """List keys under a findings prefix. Empty when the bucket or prefix is missing."""
    bucket = bucket or os.environ.get("JOURNAL_S3_BUCKET", "")
    if not bucket:
        return []
    s3 = client or _client()
    if s3 is None:
        return []
    try:
        page = s3.list_objects_v2(Bucket=bucket, Prefix=prefix)
    except Exception:  # noqa: BLE001 — missing prefix is normal
        return []
    return [str(row["Key"]) for row in page.get("Contents") or [] if row.get("Key")]


def _client() -> Any | None:
    try:
        import boto3
    except ImportError:
        return None
    return boto3.client("s3")

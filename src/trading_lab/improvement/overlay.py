"""Strategy overlay — apply only after human green-light (never auto from coaches)."""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger("trading_lab.improvement.overlay")


def load_overlay(*, bucket: str | None = None) -> dict[str, Any] | None:
    bucket = bucket or os.environ.get("JOURNAL_S3_BUCKET", "")
    if not bucket:
        return None
    try:
        import boto3
    except ImportError:
        return None
    client = boto3.client("s3")
    try:
        obj = client.get_object(Bucket=bucket, Key="strategy_overlays/latest.json")
        return json.loads(obj["Body"].read().decode())
    except Exception:  # noqa: BLE001
        return None


def write_overlay(
    overlay: dict[str, Any],
    *,
    bucket: str | None = None,
    approved_by: str = "human",
) -> dict[str, Any]:
    """Persist approved overlay. Call only after explicit green-light."""
    bucket = bucket or os.environ.get("JOURNAL_S3_BUCKET", "")
    if not bucket:
        return {"ok": False, "detail": "JOURNAL_S3_BUCKET unset"}
    try:
        import boto3
    except ImportError:
        return {"ok": False, "detail": "boto3 not installed"}
    payload = {
        **overlay,
        "approved_by": approved_by,
        "approved_at": datetime.now(timezone.utc).isoformat(),
        "immutable_guardrails": [
            "never_force_trade",
            "max_positions",
            "daily_loss",
            "budget_slices",
            "sniper_eod_flatten",
            "paper_only",
        ],
    }
    body = json.dumps(payload, indent=2).encode("utf-8")
    client = boto3.client("s3")
    keys = [
        "strategy_overlays/latest.json",
        f"strategy_overlays/{payload['approved_at'][:10]}.json",
    ]
    for key in keys:
        client.put_object(Bucket=bucket, Key=key, Body=body, ContentType="application/json")
    logger.info("wrote strategy overlay (human-approved)")
    return {"ok": True, "bucket": bucket, "keys": keys}

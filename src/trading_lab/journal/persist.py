"""Persist journal off Lambda /tmp to S3 (EOD / postmarket)."""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path


def persist_journal_to_s3(
    local_path: str | Path,
    *,
    bucket: str | None = None,
    prefix: str = "journals",
) -> dict:
    """Upload sqlite (+ optional grafana csv dir). No-op if bucket unset."""
    bucket = bucket or os.environ.get("JOURNAL_S3_BUCKET", "")
    local_path = Path(local_path)
    if not bucket:
        return {"ok": False, "detail": "JOURNAL_S3_BUCKET unset — skip persist"}
    if not local_path.exists():
        return {"ok": False, "detail": f"missing journal file: {local_path}"}

    try:
        import boto3
    except ImportError:
        return {"ok": False, "detail": "boto3 not installed"}

    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    key = f"{prefix.rstrip('/')}/{day}/{local_path.name}"
    client = boto3.client("s3")
    client.upload_file(str(local_path), bucket, key)
    return {"ok": True, "bucket": bucket, "key": key}

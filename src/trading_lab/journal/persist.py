"""Persist journal off Lambda /tmp to S3 (EOD / postmarket)."""

from __future__ import annotations

import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from trading_lab.journal.export_grafana import export_journal_csv


def persist_journal_to_s3(
    local_path: str | Path,
    *,
    bucket: str | None = None,
    prefix: str = "journals",
    grafana_prefix: str = "grafana/latest",
) -> dict:
    """Upload sqlite + Grafana CSVs (dated + latest). No-op if bucket unset."""
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
    client = boto3.client("s3")
    sqlite_key = f"{prefix.rstrip('/')}/{day}/{local_path.name}"
    client.upload_file(str(local_path), bucket, sqlite_key)

    csv_keys: dict[str, list[str]] = {"trades": [], "skips": []}
    with tempfile.TemporaryDirectory(prefix="trading-lab-grafana-") as tmp:
        paths = export_journal_csv(local_path, tmp)
        for name, path in paths.items():
            dated = f"{prefix.rstrip('/')}/{day}/{path.name}"
            latest = f"{grafana_prefix.rstrip('/')}/{path.name}"
            client.upload_file(str(path), bucket, dated)
            client.upload_file(str(path), bucket, latest)
            csv_keys[name] = [dated, latest]

    return {
        "ok": True,
        "bucket": bucket,
        "key": sqlite_key,
        "csv_keys": csv_keys,
    }

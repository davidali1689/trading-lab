"""Persist journal off Lambda /tmp to S3 (ticks + EOD / postmarket).

Lambda /tmp is per-instance: hydrate from S3 before writes, upload after so
Grafana CSV feeds and EOD see the same journal across cold starts.
"""

from __future__ import annotations

import logging
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from trading_lab.journal.export_grafana import export_journal_csv
from trading_lab.journal.open_trades import repair_ghost_reconcile_pnl
from trading_lab.journal.sqlite import SqliteJournal

logger = logging.getLogger("trading_lab.journal.persist")


def _skip_keep_days() -> int:
    try:
        return max(1, int(os.environ.get("JOURNAL_SKIP_KEEP_DAYS", "10")))
    except ValueError:
        return 10


def prune_journal(local_path: str | Path, *, keep_days: int | None = None) -> dict:
    """Bound /tmp growth: drop skip rows older than keep_days, then VACUUM.

    Lambda /tmp filled 2026-08-04 (~90MB sqlite, 123k skips) → /events 500s.
    Trades are never pruned; dated S3 copies retain full skip history.
    """
    days = keep_days if keep_days is not None else _skip_keep_days()
    cutoff = datetime.now(timezone.utc).timestamp() - days * 86400
    cutoff_iso = datetime.fromtimestamp(cutoff, tz=timezone.utc).isoformat()
    journal = SqliteJournal(local_path)
    removed = journal.delete_skips_before(cutoff_iso)
    if removed:
        logger.info("pruned %s skip rows older than %sd from journal", removed, days)
    return {"ok": True, "pruned_skips": removed, "keep_days": days}


def _latest_sqlite_key(prefix: str, name: str) -> str:
    return f"{prefix.rstrip('/')}/latest/{name}"


def hydrate_journal_from_s3(
    local_path: str | Path,
    *,
    bucket: str | None = None,
    prefix: str = "journals",
) -> dict:
    """Download journals/latest/*.sqlite into /tmp (always refresh when remote exists).

    Warm Lambda containers keep a stale /tmp sqlite; skipping download then
    persist() stomps S3 and can wipe trades written by another process.
    """
    bucket = bucket or os.environ.get("JOURNAL_S3_BUCKET", "")
    local_path = Path(local_path)
    if not bucket:
        return {"ok": False, "detail": "JOURNAL_S3_BUCKET unset — skip hydrate"}

    try:
        import boto3
        from botocore.exceptions import ClientError
    except ImportError:
        return {"ok": False, "detail": "boto3 not installed"}

    key = _latest_sqlite_key(prefix, local_path.name)
    local_path.parent.mkdir(parents=True, exist_ok=True)
    client = boto3.client("s3")
    try:
        client.download_file(bucket, key, str(local_path))
    except ClientError as exc:
        code = (exc.response.get("Error") or {}).get("Code", "")
        if code in {"404", "NoSuchKey", "NotFound", "403", "AccessDenied"}:
            # Fresh day / first run — local create happens on first write.
            return {"ok": True, "detail": f"no_remote_journal ({code or 'missing'})", "key": key}
        return {"ok": False, "detail": f"hydrate failed: {exc}", "key": key}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "detail": f"hydrate failed: {exc}", "key": key}

    logger.info("hydrated journal from s3://%s/%s", bucket, key)
    return {
        "ok": True,
        "detail": "downloaded",
        "bucket": bucket,
        "key": key,
        "path": str(local_path),
    }


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
    # Drop multiplied ghost reconcile P&L before Grafana CSV export.
    repair = repair_ghost_reconcile_pnl(local_path)
    if repair.get("zeroed"):
        logger.info("repaired ghost reconcile pnl rows: %s", repair.get("zeroed"))
    prune = prune_journal(local_path)
    sqlite_key = f"{prefix.rstrip('/')}/{day}/{local_path.name}"
    latest_sqlite = _latest_sqlite_key(prefix, local_path.name)
    client.upload_file(str(local_path), bucket, sqlite_key)
    client.upload_file(str(local_path), bucket, latest_sqlite)

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
        "latest_key": latest_sqlite,
        "csv_keys": csv_keys,
        "prune": prune,
    }

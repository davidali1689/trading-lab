"""Serve latest Grafana CSVs / JSON from S3 for Infinity datasource."""

from __future__ import annotations

import hmac
import json
import os
from typing import Any, Literal

from trading_lab.journal.export_grafana import empty_csv_header

TableName = Literal["trades", "skips", "watchlist"]
JsonName = Literal["postmortem", "watchlist", "scoreboard"]

LATEST_PREFIX = "grafana/latest"


def token_matches(provided: str | None) -> bool:
    expected = os.environ.get("GRAFANA_FEED_TOKEN", "").strip()
    if not expected:
        return False
    got = (provided or "").strip()
    # Hash first so compare_digest always sees equal-length digests.
    return hmac.compare_digest(
        hmac.new(b"tl-feed", got.encode("utf-8"), "sha256").digest(),
        hmac.new(b"tl-feed", expected.encode("utf-8"), "sha256").digest(),
    )


def fetch_latest_csv(table: TableName, *, bucket: str | None = None) -> tuple[bytes, str]:
    """Return (body, content_type) for grafana/latest/{table}.csv.

    Missing objects return a header-only CSV so Infinity panels stay green
    before the first journal persist.
    """
    bucket = bucket or os.environ.get("JOURNAL_S3_BUCKET", "")
    if not bucket:
        raise FileNotFoundError("JOURNAL_S3_BUCKET unset")
    if table not in {"trades", "skips", "watchlist"}:
        raise ValueError(f"unsupported table: {table}")

    try:
        import boto3
        from botocore.exceptions import ClientError
    except ImportError as exc:
        raise RuntimeError("boto3 not installed") from exc

    key = f"{LATEST_PREFIX}/{table}.csv"
    client = boto3.client("s3")
    try:
        obj = client.get_object(Bucket=bucket, Key=key)
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "")
        if code in {"NoSuchKey", "404", "NotFound"}:
            if table == "watchlist":
                raise FileNotFoundError(key) from exc
            return empty_csv_header(table), "text/csv; charset=utf-8"
        raise
    body = obj["Body"].read()
    return body, "text/csv; charset=utf-8"


def fetch_latest_json(name: JsonName, *, bucket: str | None = None) -> dict[str, Any]:
    """Load grafana/latest/{name}.json; raise FileNotFoundError if missing."""
    bucket = bucket or os.environ.get("JOURNAL_S3_BUCKET", "")
    if not bucket:
        raise FileNotFoundError("JOURNAL_S3_BUCKET unset")
    if name not in {"postmortem", "watchlist", "scoreboard"}:
        raise ValueError(f"unsupported json feed: {name}")

    try:
        import boto3
        from botocore.exceptions import ClientError
    except ImportError as exc:
        raise RuntimeError("boto3 not installed") from exc

    key = f"{LATEST_PREFIX}/{name}.json"
    client = boto3.client("s3")
    try:
        obj = client.get_object(Bucket=bucket, Key=key)
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "")
        if code in {"NoSuchKey", "404", "NotFound"}:
            raise FileNotFoundError(key) from exc
        raise
    raw = obj["Body"].read().decode("utf-8")
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError(f"{key} is not a JSON object")
    return data


def empty_postmortem() -> dict[str, Any]:
    return {
        "ok": False,
        "digest": {
            "trade_count": 0,
            "skip_count": 0,
            "pnl_usd_total": "0.00",
            "symbols_traded": [],
            "trades_by_agent": {},
            "skips_by_reason": {},
            "skips_by_agent": {},
        },
        "narrative": "No postmortem yet — runs after EOD persist.",
        "ts": "",
        "detail": "missing postmortem.json",
    }


def empty_scoreboard() -> dict[str, Any]:
    """Stub until first EOD daily / Friday weekly scoreboard persist."""
    from trading_lab.improvement.scoreboard import empty_scoreboard_feed

    return empty_scoreboard_feed()

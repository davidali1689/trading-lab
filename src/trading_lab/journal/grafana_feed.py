"""Serve latest Grafana CSVs from S3 for Infinity datasource."""

from __future__ import annotations

import os
from typing import Literal

TableName = Literal["trades", "skips"]

LATEST_PREFIX = "grafana/latest"


def feed_token_configured() -> bool:
    return bool(os.environ.get("GRAFANA_FEED_TOKEN", "").strip())


def token_matches(provided: str | None) -> bool:
    expected = os.environ.get("GRAFANA_FEED_TOKEN", "").strip()
    if not expected:
        return False
    return (provided or "").strip() == expected


def fetch_latest_csv(table: TableName, *, bucket: str | None = None) -> tuple[bytes, str]:
    """Return (body, content_type) for grafana/latest/{table}.csv."""
    bucket = bucket or os.environ.get("JOURNAL_S3_BUCKET", "")
    if not bucket:
        raise FileNotFoundError("JOURNAL_S3_BUCKET unset")
    if table not in {"trades", "skips"}:
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
            raise FileNotFoundError(key) from exc
        raise
    body = obj["Body"].read()
    return body, "text/csv; charset=utf-8"

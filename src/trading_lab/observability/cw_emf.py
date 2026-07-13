"""CloudWatch Embedded Metric Format helpers for near-live Grafana panels."""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from typing import Any

NAMESPACE = "TradingLab"


def emit_tick_metric(
    *,
    symbol: str,
    status: str,
    agent: str = "large_cap_sniper",
    orders: int = 0,
    skips: int = 0,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Print one EMF line to stdout (Lambda → CloudWatch Logs → metrics)."""
    payload: dict[str, Any] = {
        "_aws": {
            "Timestamp": int(datetime.now(timezone.utc).timestamp() * 1000),
            "CloudWatchMetrics": [
                {
                    "Namespace": NAMESPACE,
                    "Dimensions": [["symbol", "agent", "status"]],
                    "Metrics": [
                        {"Name": "TickCount", "Unit": "Count"},
                        {"Name": "Orders", "Unit": "Count"},
                        {"Name": "Skips", "Unit": "Count"},
                    ],
                }
            ],
        },
        "symbol": symbol or "UNKNOWN",
        "agent": agent or "unknown",
        "status": status or "UNKNOWN",
        "TickCount": 1,
        "Orders": int(orders),
        "Skips": int(skips),
    }
    if extra:
        payload.update(extra)
    # Lambda captures stdout into the log group; EMF extractor creates metrics.
    print(json.dumps(payload, separators=(",", ":")), file=sys.stdout, flush=True)
    return payload

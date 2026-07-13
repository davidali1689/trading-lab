"""Unusual Whales congressional trades — soft catalyst for swing_momentum.

API: GET /api/congress/recent-trades (Bearer token).
Docs: https://api.unusualwhales.com/docs

Never forces ENTER. Used only to skip (sell) or raise priority (buy).
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, datetime, timedelta

from trading_lab.catalysts.types import CatalystKind, CatalystSignal


def _parse_direction(txn_type: str | None) -> str | None:
    if not txn_type:
        return None
    t = txn_type.strip().lower()
    if t.startswith("buy") or t in {"purchase", "acquire"}:
        return "buy"
    if t.startswith("sell") or t in {"sale", "dispose"}:
        return "sell"
    return None


def _parse_date(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        # YYYY-MM-DD
        return datetime.strptime(value[:10], "%Y-%m-%d").replace(tzinfo=UTC)
    except ValueError:
        return None


class MockUnusualWhalesCongress:
    """Deterministic fixtures for tests / USE_MOCK paths."""

    def __init__(self, signals: list[CatalystSignal] | None = None) -> None:
        self._signals = signals or []

    def signals_for(self, symbol: str, *, since: datetime) -> list[CatalystSignal]:
        sym = symbol.upper()
        out: list[CatalystSignal] = []
        for s in self._signals:
            if s.symbol.upper() != sym:
                continue
            if s.disclosed_at.replace(tzinfo=s.disclosed_at.tzinfo or UTC) < since:
                continue
            out.append(s)
        return out


class UnusualWhalesCongress:
    """Live Unusual Whales congress recent-trades client."""

    base_url = "https://api.unusualwhales.com"

    def __init__(self, api_key: str | None = None, *, timeout_s: float = 15.0) -> None:
        self.api_key = api_key or os.environ.get("UNUSUAL_WHALES_API_KEY", "")
        self.timeout_s = timeout_s

    def signals_for(self, symbol: str, *, since: datetime) -> list[CatalystSignal]:
        if not self.api_key:
            return []
        params = urllib.parse.urlencode({"ticker": symbol.upper(), "limit": "100"})
        url = f"{self.base_url}/api/congress/recent-trades?{params}"
        req = urllib.request.Request(
            url,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Accept": "application/json",
            },
            method="GET",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:  # noqa: S310
                payload = json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, json.JSONDecodeError):
            return []

        rows = payload.get("data") if isinstance(payload, dict) else payload
        if not isinstance(rows, list):
            return []

        out: list[CatalystSignal] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            ticker = str(row.get("ticker") or "").upper()
            if ticker != symbol.upper():
                continue
            disclosed = _parse_date(row.get("filed_at_date")) or _parse_date(
                row.get("transaction_date")
            )
            if disclosed is None:
                continue
            if disclosed < since:
                continue
            direction = _parse_direction(row.get("txn_type"))
            out.append(
                CatalystSignal(
                    kind=CatalystKind.CONGRESS_TRADE,
                    symbol=ticker,
                    direction=direction,  # type: ignore[arg-type]
                    disclosed_at=disclosed,
                    transaction_date=_parse_date(row.get("transaction_date")),
                    source="unusual_whales",
                    politician=str(row.get("name") or row.get("reporter") or ""),
                    amounts=str(row.get("amounts") or ""),
                    meta={
                        "member_type": row.get("member_type"),
                        "politician_id": row.get("politician_id"),
                        "notes": row.get("notes"),
                    },
                )
            )
        return out


def load_congress_catalyst(
    *,
    mock: MockUnusualWhalesCongress | None = None,
) -> MockUnusualWhalesCongress | UnusualWhalesCongress:
    if mock is not None:
        return mock
    if os.environ.get("USE_MOCK_BARS", "").lower() in {"1", "true", "yes"}:
        return MockUnusualWhalesCongress()
    return UnusualWhalesCongress()


def congress_since(lookback_days: int) -> datetime:
    return datetime.now(tz=UTC) - timedelta(days=lookback_days)

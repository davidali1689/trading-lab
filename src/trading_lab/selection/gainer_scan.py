"""Live Alpaca gainer scan for the 09:30–10:30 ET window. Not persisted as the daily list."""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, time, timezone
from decimal import Decimal
from typing import Any

from trading_lab.market_data.alpaca_screener import AlpacaScreener, ScreenerRow
from trading_lab.schedule.market_clock import now_et
from trading_lab.selection.universe_gates import is_disallowed_product
from trading_lab.selection.watchlist import (
    WatchlistCandidate,
    WatchlistDocument,
    _last_trade_price,
    _passes_asset,
    _passes_cheap_filters,
)

logger = logging.getLogger("trading_lab.selection.gainer_scan")
GAINER_PREFIX = "gainers"

DEFAULT_SCAN_TOP = 8
MIN_DAY_GAIN_PCT = Decimal("2")
MAX_DAY_GAIN_PCT = Decimal("15")
MAX_PRICE = Decimal("50")
WINDOW_START = time(9, 30)
WINDOW_END = time(10, 30)


def gainer_scan_top() -> int:
    try:
        n = int(os.environ.get("GAINER_SCAN_TOP", str(DEFAULT_SCAN_TOP)))
    except ValueError:
        n = DEFAULT_SCAN_TOP
    return max(1, min(n, 20))


def _window_end_et() -> time:
    raw = os.environ.get("GAINER_WINDOW_END_ET", "10:30")
    try:
        hh, mm = raw.split(":", 1)
        return time(int(hh), int(mm))
    except (ValueError, AttributeError):
        return WINDOW_END


def in_gainer_window(ts: datetime | None = None) -> bool:
    clock = now_et(ts).time()
    return WINDOW_START <= clock < _window_end_et()


def is_unit_or_warrant(symbol: str) -> bool:
    """NASDAQ 5-letter root+W / root+U. Leaves NOW/LOW (3-letter) alone."""
    sym = (symbol or "").upper().strip()
    return len(sym) >= 5 and sym.endswith(("W", "U"))


def _in_early_band(pct: Decimal | None) -> bool:
    if pct is None:
        return False
    return MIN_DAY_GAIN_PCT <= pct < MAX_DAY_GAIN_PCT


def scan_live_gainers(
    screener: AlpacaScreener | None = None,
    *,
    verify_assets: bool = True,
    top: int | None = None,
) -> list[ScreenerRow]:
    """Alpaca movers → early-band liquid common equity. Empty on vendor failure."""
    top = top or gainer_scan_top()
    client = screener or AlpacaScreener()
    try:
        movers = client.movers(top=max(top * 2, 20))
    except Exception:  # noqa: BLE001 — never invent a list
        return []

    kept: list[ScreenerRow] = []
    for row in movers:
        if row.source != "gainer":
            continue
        if is_unit_or_warrant(row.symbol):
            continue
        if is_disallowed_product(row.symbol):
            continue
        price = row.price if row.price is not None else _last_trade_price(client, row.symbol)
        if price is None or price > MAX_PRICE:
            continue
        ok, _reason = _passes_cheap_filters(row, price=price)
        if not ok:
            continue
        if not _in_early_band(row.percent_change):
            continue
        if verify_assets:
            meta = client.asset(row.symbol)
            ok, _reason = _passes_asset(meta)
            if not ok:
                continue
            if is_disallowed_product(row.symbol, meta=meta):
                continue
        kept.append(
            ScreenerRow(
                symbol=row.symbol,
                source=row.source,
                price=price,
                volume=row.volume,
                percent_change=row.percent_change,
                trade_count=row.trade_count,
            )
        )
        if len(kept) >= top:
            break
    return kept


def union_tick_symbols(
    watchlist_symbols: list[str],
    live_gainer_symbols: list[str],
    *,
    now_et: datetime | None = None,
    extra_cap: int | None = None,
) -> list[str]:
    """Watchlist first; append live gainers only during the window (deduped)."""
    seen: list[str] = []
    for raw in watchlist_symbols:
        sym = str(raw).upper().strip()
        if sym and sym not in seen:
            seen.append(sym)
    if not in_gainer_window(now_et):
        return seen
    cap = extra_cap if extra_cap is not None else gainer_scan_top()
    added = 0
    for raw in live_gainer_symbols:
        if added >= cap:
            break
        sym = str(raw).upper().strip()
        if not sym or sym in seen:
            continue
        seen.append(sym)
        added += 1
    return seen


def snapshot_candidates(rows: list[ScreenerRow]) -> list[WatchlistCandidate]:
    return [
        WatchlistCandidate(
            symbol=r.symbol,
            sources=["gainer"],
            price=str(r.price) if r.price is not None else None,
            volume=str(r.volume) if r.volume is not None else None,
            percent_change=str(r.percent_change) if r.percent_change is not None else None,
            reason="live_gainer_early_band",
        )
        for r in rows
    ]


def live_gainer_pct_map(rows: list[ScreenerRow]) -> dict[str, Decimal]:
    out: dict[str, Decimal] = {}
    for row in rows:
        if row.percent_change is None:
            continue
        out[row.symbol.upper()] = row.percent_change
    return out


def persist_first_hour_snapshot(
    rows: list[ScreenerRow],
    *,
    bucket: str | None = None,
    prefix: str = GAINER_PREFIX,
) -> dict[str, Any]:
    """Write gainers/{day}/first_hour.json so miss harvest can join EOD vs early band."""
    bucket = bucket or os.environ.get("JOURNAL_S3_BUCKET", "")
    if not bucket:
        return {"ok": False, "detail": "JOURNAL_S3_BUCKET unset — skip gainer snapshot"}
    try:
        import boto3
    except ImportError:
        return {"ok": False, "detail": "boto3 not installed"}

    cands = snapshot_candidates(rows)
    existing = load_first_hour_snapshot(bucket=bucket, prefix=prefix)
    first_seen = {
        c.symbol: c
        for c in existing.candidates
        if c.symbol
    }
    merged: list[WatchlistCandidate] = []
    seen: set[str] = set()
    for cand in [*existing.candidates, *cands]:
        if cand.symbol in seen:
            continue
        seen.add(cand.symbol)
        if cand.symbol in first_seen:
            merged.append(first_seen[cand.symbol])
        else:
            merged.append(cand)

    doc = WatchlistDocument(
        symbols=[c.symbol for c in merged],
        candidates=merged,
        source="fresh_scan",
        built_at=datetime.now(timezone.utc).isoformat(),
        size=len(merged),
        detail=f"first_hour_gainers={len(merged)} tick_rows={len(rows)}",
    )
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    body = json.dumps(doc.to_dict(), indent=2).encode("utf-8")
    client = boto3.client("s3")
    dated_key = f"{prefix.rstrip('/')}/{day}/first_hour.json"
    latest_key = f"{prefix.rstrip('/')}/latest.json"
    client.put_object(Bucket=bucket, Key=dated_key, Body=body, ContentType="application/json")
    client.put_object(Bucket=bucket, Key=latest_key, Body=body, ContentType="application/json")
    logger.info("wrote first-hour gainer snapshot symbols=%s", doc.symbols)
    return {"ok": True, "bucket": bucket, "keys": [dated_key, latest_key], "symbols": doc.symbols}


def load_first_hour_snapshot(
    *,
    bucket: str | None = None,
    prefix: str = GAINER_PREFIX,
    day: str | None = None,
) -> WatchlistDocument:
    empty = WatchlistDocument(
        symbols=[],
        candidates=[],
        source="empty",
        built_at=datetime.now(timezone.utc).isoformat(),
        size=0,
        detail="no_first_hour_snapshot",
    )
    bucket = bucket or os.environ.get("JOURNAL_S3_BUCKET", "")
    if not bucket:
        empty.detail = "JOURNAL_S3_BUCKET unset"
        return empty
    try:
        import boto3
    except ImportError:
        empty.detail = "boto3 not installed"
        return empty
    key = (
        f"{prefix.rstrip('/')}/{day}/first_hour.json"
        if day
        else f"{prefix.rstrip('/')}/latest.json"
    )
    try:
        client = boto3.client("s3")
        obj = client.get_object(Bucket=bucket, Key=key)
        data = json.loads(obj["Body"].read().decode("utf-8"))
    except Exception as exc:  # noqa: BLE001
        empty.detail = f"s3_load_failed: {exc}"
        return empty
    return WatchlistDocument.from_dict(data, source="s3")

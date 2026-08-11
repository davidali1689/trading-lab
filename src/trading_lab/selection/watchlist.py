"""Dynamic daily watchlist — Alpaca screener + strategy filters; S3 persist."""

from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Literal

from trading_lab.config.vendors import V1_VENDORS
from trading_lab.market_data.alpaca_screener import AlpacaScreener, ScreenerRow
from trading_lab.market_data.types import BarRequest, MarketDataPort
from trading_lab.selection.universe_gates import day_gain_too_extended, is_disallowed_product

logger = logging.getLogger("trading_lab.selection.watchlist")

WatchlistSource = Literal["s3", "fresh_scan", "empty"]

MIN_PRICE = Decimal("5")
OTC_EXCHANGES = {"OTC", "OTCBB", "PINK"}
# Prefer liquid large-name heuristics without a market-cap vendor.
MIN_ACTIVE_VOLUME = Decimal("1000000")


@dataclass
class WatchlistCandidate:
    symbol: str
    status: str = "CANDIDATE"
    sources: list[str] = field(default_factory=list)
    price: str | None = None
    volume: str | None = None
    percent_change: str | None = None
    reason: str = "screener_pass"
    # Alpaca asset name — lets ticks re-check leveraged/ETF products by name
    # without an extra API call (2026-08-11: tick-time check ran symbol-only).
    name: str | None = None


@dataclass
class WatchlistDocument:
    symbols: list[str]
    candidates: list[WatchlistCandidate]
    source: WatchlistSource
    built_at: str
    size: int
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbols": self.symbols,
            "candidates": [asdict(c) for c in self.candidates],
            "source": self.source,
            "built_at": self.built_at,
            "size": self.size,
            "detail": self.detail,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any], *, source: WatchlistSource) -> WatchlistDocument:
        cands = [
            WatchlistCandidate(
                **{k: v for k, v in row.items() if k in WatchlistCandidate.__dataclass_fields__}
            )
            for row in (data.get("candidates") or [])
            if isinstance(row, dict) and row.get("symbol")
        ]
        symbols = [str(s).upper() for s in (data.get("symbols") or []) if str(s).strip()]
        if not symbols and cands:
            symbols = [c.symbol for c in cands]
        return cls(
            symbols=symbols,
            candidates=cands,
            source=source,
            built_at=str(data.get("built_at") or ""),
            size=int(data.get("size") or len(symbols)),
            detail=str(data.get("detail") or ""),
        )


def watchlist_size() -> int:
    raw = os.environ.get("WATCHLIST_SIZE", "12")
    try:
        n = int(raw)
    except ValueError:
        n = 12
    return max(1, min(n, 50))


def _looks_like_common_equity(symbol: str) -> bool:
    """Drop preferreds / warrants / units by ticker shape (no hardcoded names)."""
    if not symbol or not symbol.isalpha():
        return False
    if len(symbol) > 5:
        return False
    return True


def _merge_rows(rows: list[ScreenerRow]) -> dict[str, ScreenerRow]:
    """One row per symbol; prefer mover price + keep highest volume."""
    merged: dict[str, ScreenerRow] = {}
    for row in rows:
        prev = merged.get(row.symbol)
        if prev is None:
            merged[row.symbol] = row
            continue
        sources = {prev.source, row.source}
        # Prefer non-loser price; keep max volume / |pct|
        price = row.price if row.price is not None else prev.price
        volume = prev.volume
        if row.volume is not None and (volume is None or row.volume > volume):
            volume = row.volume
        pct = prev.percent_change
        if row.percent_change is not None and (pct is None or abs(row.percent_change) > abs(pct)):
            pct = row.percent_change
        # Prefer gainer/most_actives label for ranking later
        source = prev.source
        if "gainer" in sources:
            source = "gainer"
        elif "most_actives" in sources:
            source = "most_actives"
        elif row.source != "loser":
            source = row.source
        merged[row.symbol] = ScreenerRow(
            symbol=row.symbol,
            source=source,
            price=price,
            volume=volume,
            percent_change=pct,
            trade_count=row.trade_count or prev.trade_count,
        )
    return merged


def _rank_key(row: ScreenerRow) -> tuple:
    source_rank = {"gainer": 0, "most_actives": 1, "loser": 2}.get(row.source, 9)
    vol = row.volume or Decimal("0")
    pct = abs(row.percent_change or Decimal("0"))
    return (source_rank, -vol, -pct)


def _last_trade_price(client: Any, symbol: str) -> Decimal | None:
    """Resolve price when screener row has none (most_actives). None on failure."""
    fn = getattr(client, "last_trade_price", None)
    if fn is None:
        return None
    try:
        px = fn(symbol)
    except Exception:  # noqa: BLE001 — fail closed on vendor errors
        return None
    if px is None:
        return None
    try:
        price = Decimal(str(px))
    except Exception:  # noqa: BLE001
        return None
    return price if price > 0 else None


def _passes_cheap_filters(row: ScreenerRow, *, price: Decimal | None = None) -> tuple[bool, str]:
    if not _looks_like_common_equity(row.symbol):
        return False, "not_common_equity_ticker"
    if is_disallowed_product(row.symbol):
        return False, "disallowed_product"
    px = price if price is not None else row.price
    # Fail closed: unknown price must not bypass the penny floor (2026-08-04: ENSC/ZBAO).
    if px is None:
        return False, "price_unresolved"
    if px < MIN_PRICE:
        return False, f"price<{MIN_PRICE}"
    # 2026-08-05: already-extended day gainers (AMIX +434%) are chase, not edge.
    if day_gain_too_extended(row.percent_change):
        return False, "day_gain_extended"
    # Most-actives often lack price; volume floor when present
    if row.volume is not None and row.volume < MIN_ACTIVE_VOLUME and row.source == "most_actives":
        return False, "volume_too_low"
    return True, "ok"


def min_watchlist_prev_bars() -> int:
    """1Min bars required over the prior 24h to earn a watchlist slot. 0 disables.

    2026-08-11: ALGS sat on the list all day returning 0–1 bars per tick (IEX
    feed, ultra-thin tape) — it can never reach the 21-bar evaluation minimum,
    so it only starves the speculative sniper of a usable slot.
    """
    try:
        return max(0, int(os.environ.get("MIN_WATCHLIST_PREV_BARS", "100")))
    except ValueError:
        return 100


def _bar_coverage(md: MarketDataPort, symbol: str) -> int | None:
    """1Min bar count over the last 24h; None = data unavailable (gate fails open)."""
    end = datetime.now(timezone.utc)
    start = end - timedelta(hours=24)
    try:
        bars = md.get_bars(
            BarRequest(
                symbol=symbol,
                timeframe="1Min",
                start=start,
                end=end,
                feed=V1_VENDORS.alpaca_feed,
            )
        )
    except Exception:  # noqa: BLE001 — coverage is an optimization, not a safety gate
        return None
    return len(bars)


def _passes_asset(meta: Any) -> tuple[bool, str]:
    if meta is None:
        return False, "asset_lookup_failed"
    if not meta.tradable or meta.status != "active":
        return False, "not_tradable"
    if meta.asset_class and meta.asset_class not in {"us_equity", ""}:
        return False, f"asset_class={meta.asset_class}"
    if meta.exchange.upper() in OTC_EXCHANGES:
        return False, "otc"
    if is_disallowed_product(meta.symbol, meta=meta):
        return False, "disallowed_product"
    return True, "ok"


def build_daily_watchlist(
    *,
    screener: AlpacaScreener | None = None,
    size: int | None = None,
    verify_assets: bool = True,
    market_data: MarketDataPort | None = None,
) -> WatchlistDocument:
    """Scan Alpaca movers/actives → strategy filters → candidate list (no ENTERs)."""
    size = size or watchlist_size()
    built_at = datetime.now(timezone.utc).isoformat()
    client = screener or AlpacaScreener()

    min_bars = min_watchlist_prev_bars()
    md: MarketDataPort | None = market_data
    if min_bars > 0 and md is None:
        try:
            from trading_lab.market_data.factory import resolve_market_data

            md = resolve_market_data()
        except Exception:  # noqa: BLE001 — no data backend → coverage gate fails open
            md = None

    try:
        actives = client.most_actives(top=max(size * 2, 25))
        movers = client.movers(top=max(size, 20))
    except Exception as exc:  # noqa: BLE001 — scan failure → empty, never hardcoded
        logger.exception("watchlist scan failed")
        return WatchlistDocument(
            symbols=[],
            candidates=[],
            source="empty",
            built_at=built_at,
            size=0,
            detail=f"scan_failed: {exc}",
        )

    merged = _merge_rows([*actives, *movers])
    shortlist: list[tuple[ScreenerRow, Decimal]] = []
    rejected: list[str] = []
    for row in sorted(merged.values(), key=_rank_key):
        price = row.price if row.price is not None else _last_trade_price(client, row.symbol)
        ok, reason = _passes_cheap_filters(row, price=price)
        if not ok or price is None:
            rejected.append(f"{row.symbol}:{reason}")
            continue
        shortlist.append((row, price))

    candidates: list[WatchlistCandidate] = []
    for row, price in shortlist:
        if len(candidates) >= size:
            break
        asset_name: str | None = None
        if verify_assets:
            meta = client.asset(row.symbol)
            ok, reason = _passes_asset(meta)
            if not ok:
                rejected.append(f"{row.symbol}:{reason}")
                continue
            asset_name = (getattr(meta, "name", None) or None) if meta is not None else None
        if min_bars > 0 and md is not None:
            coverage = _bar_coverage(md, row.symbol)
            if coverage is not None and coverage < min_bars:
                rejected.append(f"{row.symbol}:bar_coverage={coverage}<{min_bars}")
                continue
        # Track all screener sources that mentioned this symbol
        sources = sorted({r.source for r in [*actives, *movers] if r.symbol == row.symbol})
        candidates.append(
            WatchlistCandidate(
                symbol=row.symbol,
                sources=sources,
                price=str(price),
                volume=str(row.volume) if row.volume is not None else None,
                percent_change=str(row.percent_change) if row.percent_change is not None else None,
                reason="screener_pass",
                name=asset_name,
            )
        )

    symbols = [c.symbol for c in candidates]
    detail = (
        f"candidates={len(symbols)} scanned={len(merged)} rejected={len(rejected)}"
        if symbols
        else f"empty_after_filters scanned={len(merged)} rejected={len(rejected)}"
    )
    return WatchlistDocument(
        symbols=symbols,
        candidates=candidates,
        source="fresh_scan" if symbols else "empty",
        built_at=built_at,
        size=len(symbols),
        detail=detail,
    )


def save_watchlist(
    doc: WatchlistDocument,
    *,
    bucket: str | None = None,
    prefix: str = "watchlists",
) -> dict[str, Any]:
    bucket = bucket or os.environ.get("JOURNAL_S3_BUCKET", "")
    if not bucket:
        return {"ok": False, "detail": "JOURNAL_S3_BUCKET unset — skip watchlist save"}
    try:
        import boto3
    except ImportError:
        return {"ok": False, "detail": "boto3 not installed"}

    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    body = json.dumps(doc.to_dict(), indent=2).encode("utf-8")
    client = boto3.client("s3")
    dated_key = f"{prefix.rstrip('/')}/{day}.json"
    latest_key = f"{prefix.rstrip('/')}/latest.json"
    client.put_object(Bucket=bucket, Key=dated_key, Body=body, ContentType="application/json")
    client.put_object(Bucket=bucket, Key=latest_key, Body=body, ContentType="application/json")
    return {
        "ok": True,
        "bucket": bucket,
        "keys": [dated_key, latest_key],
        "symbols": doc.symbols,
    }


def load_watchlist(
    *,
    bucket: str | None = None,
    prefix: str = "watchlists",
) -> WatchlistDocument:
    bucket = bucket or os.environ.get("JOURNAL_S3_BUCKET", "")
    empty = WatchlistDocument(
        symbols=[],
        candidates=[],
        source="empty",
        built_at=datetime.now(timezone.utc).isoformat(),
        size=0,
        detail="no_watchlist",
    )
    if not bucket:
        empty.detail = "JOURNAL_S3_BUCKET unset"
        return empty
    try:
        import boto3
    except ImportError:
        empty.detail = "boto3 not installed"
        return empty

    key = f"{prefix.rstrip('/')}/latest.json"
    try:
        client = boto3.client("s3")
        obj = client.get_object(Bucket=bucket, Key=key)
        data = json.loads(obj["Body"].read().decode("utf-8"))
    except Exception as exc:  # noqa: BLE001
        empty.detail = f"s3_load_failed: {exc}"
        return empty

    doc = WatchlistDocument.from_dict(data, source="s3")
    if not doc.symbols:
        doc.source = "empty"
        doc.detail = doc.detail or "s3_empty"
    return doc


def get_watchlist(*, refresh: bool = False) -> WatchlistDocument:
    """Load S3 latest for ticks; optional refresh rebuilds from screener."""
    if refresh:
        doc = build_daily_watchlist()
        if doc.symbols:
            save_watchlist(doc)
        return doc
    return load_watchlist()

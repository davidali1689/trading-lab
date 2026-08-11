"""Swing universe — daily-bar momentum scan, separate from the intraday movers list.

2026-08-11: swing_momentum went a week without a candidate because it was fed
the intraday spike watchlist and judged it on daily-bar gates (rvol / 8-EMA).
This scan screens a wider slice of the tape on the gates swing actually uses,
so the historically best agent sees real multi-day setups.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from trading_lab.config.vendors import V1_VENDORS
from trading_lab.market_data.alpaca_screener import AlpacaScreener
from trading_lab.market_data.types import Bar, BarRequest, MarketDataPort
from trading_lab.selection.watchlist import (
    WatchlistCandidate,
    WatchlistDocument,
    _last_trade_price,
    _merge_rows,
    _passes_asset,
    _passes_cheap_filters,
    _rank_key,
    load_watchlist,
    save_watchlist,
)

logger = logging.getLogger("trading_lab.selection.swing_watchlist")

SWING_PREFIX = "watchlists/swing"


def swing_watchlist_size() -> int:
    try:
        n = int(os.environ.get("SWING_WATCHLIST_SIZE", "12"))
    except ValueError:
        n = 12
    return max(1, min(n, 50))


def swing_min_daily_rvol() -> Decimal:
    raw = os.environ.get("SWING_MIN_DAILY_RVOL", "1.5")
    try:
        return Decimal(raw)
    except Exception:  # noqa: BLE001
        return Decimal("1.5")


def _ema(values: list[Decimal], span: int) -> Decimal:
    k = Decimal(2) / Decimal(span + 1)
    ema = values[0]
    for v in values[1:]:
        ema = v * k + ema * (1 - k)
    return ema


def _passes_daily_momentum(bars: list[Bar]) -> tuple[bool, str]:
    """Swing entry gates on daily bars: 21+ bars, close > 8-EMA, daily RVOL."""
    if len(bars) < 21:
        return False, f"daily_bars={len(bars)}<21"
    closes = [b.close for b in bars]
    if closes[-1] <= _ema(closes[-20:], 8):
        return False, "below_8ema"
    vols = [b.volume for b in bars]
    avg20 = sum(vols[-21:-1], Decimal("0")) / Decimal(20)
    if avg20 <= 0:
        return False, "no_volume_history"
    rvol = vols[-1] / avg20
    if rvol < swing_min_daily_rvol():
        return False, f"daily_rvol={rvol.quantize(Decimal('0.01'))}<{swing_min_daily_rvol()}"
    return True, "ok"


def _daily_bars(md: MarketDataPort, symbol: str) -> list[Bar] | None:
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=45)
    try:
        return md.get_bars(
            BarRequest(
                symbol=symbol,
                timeframe="1Day",
                start=start,
                end=end,
                feed=V1_VENDORS.alpaca_feed,
            )
        )
    except Exception:  # noqa: BLE001 — vendor failure → symbol just doesn't qualify
        return None


def build_swing_watchlist(
    *,
    screener: AlpacaScreener | None = None,
    market_data: MarketDataPort | None = None,
    size: int | None = None,
    verify_assets: bool = True,
) -> WatchlistDocument:
    """Scan a wide actives/movers slice → daily momentum gates → swing candidates."""
    size = size or swing_watchlist_size()
    built_at = datetime.now(timezone.utc).isoformat()
    client = screener or AlpacaScreener()

    if market_data is None:
        from trading_lab.market_data.factory import resolve_market_data

        market_data = resolve_market_data()

    try:
        actives = client.most_actives(top=50)
        movers = client.movers(top=20)
    except Exception as exc:  # noqa: BLE001 — scan failure → empty, never hardcoded
        logger.exception("swing watchlist scan failed")
        return WatchlistDocument(
            symbols=[],
            candidates=[],
            source="empty",
            built_at=built_at,
            size=0,
            detail=f"scan_failed: {exc}",
        )

    merged = _merge_rows([*actives, *movers])
    candidates: list[WatchlistCandidate] = []
    rejected: list[str] = []
    for row in sorted(merged.values(), key=_rank_key):
        if len(candidates) >= size:
            break
        price = row.price if row.price is not None else _last_trade_price(client, row.symbol)
        ok, reason = _passes_cheap_filters(row, price=price)
        if not ok or price is None:
            rejected.append(f"{row.symbol}:{reason}")
            continue
        bars = _daily_bars(market_data, row.symbol)
        if bars is None:
            rejected.append(f"{row.symbol}:daily_bars_unavailable")
            continue
        ok, reason = _passes_daily_momentum(bars)
        if not ok:
            rejected.append(f"{row.symbol}:{reason}")
            continue
        asset_name: str | None = None
        if verify_assets:
            meta = client.asset(row.symbol)
            ok, reason = _passes_asset(meta)
            if not ok:
                rejected.append(f"{row.symbol}:{reason}")
                continue
            asset_name = (getattr(meta, "name", None) or None) if meta is not None else None
        candidates.append(
            WatchlistCandidate(
                symbol=row.symbol,
                sources=sorted({r.source for r in [*actives, *movers] if r.symbol == row.symbol}),
                price=str(price),
                volume=str(row.volume) if row.volume is not None else None,
                percent_change=str(row.percent_change) if row.percent_change is not None else None,
                reason="swing_scan_pass",
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


def save_swing_watchlist(doc: WatchlistDocument) -> dict[str, Any]:
    return save_watchlist(doc, prefix=SWING_PREFIX)


def get_swing_watchlist(*, refresh: bool = False) -> WatchlistDocument:
    """Load S3 latest swing list; optional refresh rebuilds from the scan."""
    if refresh:
        doc = build_swing_watchlist()
        if doc.symbols:
            save_swing_watchlist(doc)
        return doc
    return load_watchlist(prefix=SWING_PREFIX)

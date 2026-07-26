"""Deterministic missed-gainer harvest → S3 (postmarket). No LLM, no orders."""

from __future__ import annotations

import json
import logging
import os
import sqlite3
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from trading_lab.agents import AGENTS
from trading_lab.market_data.alpaca_screener import AlpacaScreener, ScreenerRow
from trading_lab.pipeline.paper_agents import resolve_market_cap, resolve_sniper_agent
from trading_lab.schemas.misses import DailyMissReport, MissBucket, MissRecord
from trading_lab.selection.watchlist import MIN_PRICE, get_watchlist

logger = logging.getLogger("trading_lab.improvement.miss_harvest")

DEFAULT_TOP_N = 20
AGENT_IDS = tuple(AGENTS.keys())


def _top_n() -> int:
    raw = os.environ.get("MISS_HARVEST_TOP_N", str(DEFAULT_TOP_N))
    try:
        return max(5, min(int(raw), 50))
    except ValueError:
        return DEFAULT_TOP_N


def _journal_symbols(db_path: str | Path) -> tuple[set[str], dict[str, list[str]], dict[str, str]]:
    """Return traded symbols, skip_reasons by symbol, best pnl_pct by symbol."""
    db_path = Path(db_path)
    traded: set[str] = set()
    skips: dict[str, list[str]] = {}
    pnl: dict[str, str] = {}
    if not db_path.exists():
        return traded, skips, pnl
    with sqlite3.connect(db_path) as conn:
        for row in conn.execute("SELECT symbol, found_by_agent, pnl_pct FROM trades"):
            sym = str(row[0]).upper()
            traded.add(sym)
            # keep highest pnl string for bucket C heuristics
            prev = pnl.get(sym)
            try:
                cur = Decimal(str(row[2] or "0"))
                if prev is None or cur > Decimal(str(prev)):
                    pnl[sym] = str(row[2] or "0")
            except Exception:  # noqa: BLE001
                pnl[sym] = str(row[2] or "0")
        for row in conn.execute("SELECT symbol, skip_reason FROM skips"):
            sym = str(row[0]).upper()
            skips.setdefault(sym, []).append(str(row[1]))
    return traded, skips, pnl


def _liquid_gainers(
    screener: AlpacaScreener | None,
    *,
    top: int,
    injected: list[ScreenerRow] | None = None,
) -> list[ScreenerRow]:
    if injected is not None:
        rows = [r for r in injected if r.source == "gainer"]
    else:
        client = screener or AlpacaScreener()
        try:
            rows = [r for r in client.movers(top=top) if r.source == "gainer"]
        except Exception as exc:  # noqa: BLE001
            logger.exception("miss harvest movers failed")
            raise RuntimeError(f"movers_failed: {exc}") from exc
    filtered: list[ScreenerRow] = []
    for row in rows:
        if row.price is not None and row.price < MIN_PRICE:
            continue
        if not row.symbol.isalpha() or len(row.symbol) > 5:
            continue
        filtered.append(row)
    filtered.sort(key=lambda r: -(r.percent_change or Decimal("0")))
    return filtered[:top]


def _classify(
    symbol: str,
    *,
    watchlist: set[str],
    traded: set[str],
    skips: dict[str, list[str]],
    pnl: dict[str, str],
    percent_change: Decimal | None,
) -> tuple[MissBucket, str]:
    if symbol not in watchlist and symbol not in traded and symbol not in skips:
        return MissBucket.NEVER_WATCHLIST, "never_on_watchlist_or_journal"
    if symbol in traded:
        try:
            trade_pct = Decimal(pnl.get(symbol) or "0")
            gainer_pct = percent_change or Decimal("0")
            # Captured meaningfully if PnL ≥ 25% of the day's gain (long bias)
            if gainer_pct > 0 and trade_pct >= gainer_pct * Decimal("0.25"):
                return MissBucket.ENTERED_MISSED_MOVE, "traded_but_capture_check"
            if trade_pct <= 0:
                return MissBucket.ENTERED_MISSED_MOVE, "traded_non_positive_pnl"
            return MissBucket.ENTERED_MISSED_MOVE, "traded_weak_capture_vs_gainer"
        except Exception:  # noqa: BLE001
            return MissBucket.ENTERED_MISSED_MOVE, "traded_pnl_parse_error"
    return MissBucket.WATCHED_NO_ENTER, "on_watchlist_or_skipped_no_enter"


def _is_true_miss(bucket: MissBucket, detail: str) -> bool:
    if bucket == MissBucket.NEVER_WATCHLIST:
        return True
    if bucket == MissBucket.WATCHED_NO_ENTER:
        return True
    # C: only count weak / non-positive capture as miss for top-miss ranking
    return detail != "traded_but_capture_check"


def build_miss_report(
    *,
    journal_path: str | Path,
    screener: AlpacaScreener | None = None,
    injected_gainers: list[ScreenerRow] | None = None,
    watchlist_symbols: list[str] | None = None,
    day: str | None = None,
    top_n: int | None = None,
) -> DailyMissReport:
    """Build A/B/C miss report + per-strategy #1 miss (all four agents)."""
    top_n = top_n or _top_n()
    built_at = datetime.now(timezone.utc).isoformat()
    day = day or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if watchlist_symbols is None:
        wl = get_watchlist()
        watchlist_symbols = list(wl.symbols)
    watch_set = {s.upper() for s in watchlist_symbols}
    traded, skips, pnl = _journal_symbols(journal_path)

    try:
        gainers = _liquid_gainers(screener, top=top_n, injected=injected_gainers)
    except RuntimeError as exc:
        return DailyMissReport(
            day=day,
            built_at=built_at,
            detail=str(exc),
            watchlist_symbols=sorted(watch_set),
            traded_symbols=sorted(traded),
        )

    records: list[MissRecord] = []
    for row in gainers:
        bucket, detail = _classify(
            row.symbol,
            watchlist=watch_set,
            traded=traded,
            skips=skips,
            pnl=pnl,
            percent_change=row.percent_change,
        )
        # Cap from Finnhub (via resolve_market_cap) so mid/large get real ownership.
        owner = resolve_sniper_agent(resolve_market_cap(row.symbol), row.symbol)
        traded_by = []
        # attribute trades from journal if we re-query — lightweight: any trade ⇒ unknown agents
        if row.symbol in traded:
            traded_by = _agents_for_symbol(journal_path, row.symbol)
        rec = MissRecord(
            symbol=row.symbol,
            percent_change=str(row.percent_change) if row.percent_change is not None else None,
            price=str(row.price) if row.price is not None else None,
            volume=str(row.volume) if row.volume is not None else None,
            bucket=bucket,
            owner_sniper=owner,
            skip_reasons=sorted(set(skips.get(row.symbol, []))),
            traded_by=traded_by,
            trade_pnl_pct=pnl.get(row.symbol),
            detail=detail,
        )
        if _is_true_miss(bucket, detail) or bucket == MissBucket.ENTERED_MISSED_MOVE:
            # drop only "strong capture" from miss ranking
            if detail == "traded_but_capture_check":
                continue
            records.append(rec)

    per_agent: dict[str, MissRecord | None] = {aid: None for aid in AGENT_IDS}
    for aid in AGENT_IDS:
        if aid == "swing_momentum":
            pool = records  # swing owns all tiers
        else:
            pool = [r for r in records if r.owner_sniper == aid]
        if not pool:
            per_agent[aid] = None
            continue
        per_agent[aid] = max(
            pool,
            key=lambda r: Decimal(r.percent_change or "0"),
        )

    return DailyMissReport(
        day=day,
        built_at=built_at,
        top_gainers=records,
        per_agent_top_miss=per_agent,
        watchlist_symbols=sorted(watch_set),
        traded_symbols=sorted(traded),
        detail=f"misses={len(records)} gainers_scanned={len(gainers)} top_n={top_n}",
    )


def _agents_for_symbol(db_path: str | Path, symbol: str) -> list[str]:
    db_path = Path(db_path)
    if not db_path.exists():
        return []
    agents: list[str] = []
    with sqlite3.connect(db_path) as conn:
        for row in conn.execute(
            "SELECT DISTINCT found_by_agent FROM trades WHERE symbol = ?",
            (symbol.upper(),),
        ):
            agents.append(str(row[0]))
    return agents


def persist_miss_report(
    report: DailyMissReport,
    *,
    bucket: str | None = None,
) -> dict[str, Any]:
    """Write misses/{day}/report.json + misses/latest/report.json."""
    bucket = bucket or os.environ.get("JOURNAL_S3_BUCKET", "")
    body = json.dumps(report.to_dict(), indent=2)
    if not bucket:
        return {
            "ok": False,
            "detail": "JOURNAL_S3_BUCKET unset — report not uploaded",
            "local": True,
        }

    try:
        import boto3
    except ImportError:
        return {"ok": False, "detail": "boto3 not installed"}

    client = boto3.client("s3")
    keys = [
        f"misses/{report.day}/report.json",
        "misses/latest/report.json",
    ]
    for key in keys:
        client.put_object(
            Bucket=bucket,
            Key=key,
            Body=body.encode("utf-8"),
            ContentType="application/json",
        )
    # Per-agent shards for coach retrieval
    for agent_id, miss in report.per_agent_top_miss.items():
        shard = {
            "day": report.day,
            "agent_id": agent_id,
            "top_miss": miss.model_dump(mode="json") if miss else None,
            "related": [
                r.model_dump(mode="json")
                for r in report.top_gainers
                if agent_id == "swing_momentum" or r.owner_sniper == agent_id
            ][:10],
        }
        client.put_object(
            Bucket=bucket,
            Key=f"misses/{report.day}/by_agent/{agent_id}.json",
            Body=json.dumps(shard, indent=2).encode("utf-8"),
            ContentType="application/json",
        )
    logger.info("persisted miss report s3://%s/misses/%s/", bucket, report.day)
    return {"ok": True, "bucket": bucket, "keys": keys, "day": report.day}


def run_and_persist_miss_harvest(
    journal_path: str | Path,
    *,
    screener: AlpacaScreener | None = None,
    injected_gainers: list[ScreenerRow] | None = None,
) -> dict[str, Any]:
    report = build_miss_report(
        journal_path=journal_path,
        screener=screener,
        injected_gainers=injected_gainers,
    )
    persist = persist_miss_report(report)
    return {"ok": persist.get("ok", False), "report": report.to_dict(), "persist": persist}

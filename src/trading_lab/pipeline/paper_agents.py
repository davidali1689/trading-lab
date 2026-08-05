"""Multi-agent paper tick: route sniper by cap + swing power-hour orders."""

from __future__ import annotations

import logging
import os
from datetime import datetime, time, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

from trading_lab.agents.sniper.large_cap import LARGE_CAP_SNIPER
from trading_lab.agents.sniper.mid_cap import MID_CAP_SNIPER
from trading_lab.agents.sniper.shared_execution import SniperStatus
from trading_lab.agents.sniper.speculative import SPECULATIVE_SNIPER
from trading_lab.broker.alpaca import AlpacaPaperBroker
from trading_lab.catalysts.finnhub_news import has_finnhub_key, symbol_has_recent_news
from trading_lab.catalysts.finnhub_profile import fetch_market_cap_usd
from trading_lab.config.vendors import V1_VENDORS
from trading_lab.eval.large_cap import evaluate_large_cap_sniper
from trading_lab.eval.mid_cap import evaluate_mid_cap_sniper
from trading_lab.eval.speculative import evaluate_speculative_sniper
from trading_lab.execution.budget import agent_slice_notional
from trading_lab.journal.open_trades import load_open_plans
from trading_lab.journal.sqlite import SqliteJournal
from trading_lab.market_data.factory import resolve_market_data
from trading_lab.market_data.types import BarRequest, SessionContext
from trading_lab.pipeline.paper_submit import (
    make_risk_gate,
    qty_for_price,
    submit_paper_intent,
    write_skip,
)
from trading_lab.pipeline.swing_tick import run_swing_paper_tick
from trading_lab.schedule import swing_power_hour
from trading_lab.schedule.market_clock import now_et
from trading_lab.schemas.trades import RunMode, SkipReason
from trading_lab.selection.universe_gates import (
    is_disallowed_product,
    max_open_large_cap,
    speculative_day_gain_too_extended,
)
from trading_lab.selection.watchlist import get_watchlist

logger = logging.getLogger("trading_lab.paper_agents")


def _paper_has_catalyst(symbol: str) -> bool:
    """Finnhub company-news in last 48h. No key → False (catalyst required on paper)."""
    if not has_finnhub_key():
        logger.warning("FINNHUB_API_KEY missing — sniper catalyst gate will fail closed")
        return False
    return symbol_has_recent_news(symbol)


def _above_20dma(bars: list) -> bool | None:
    if len(bars) < 20:
        return None
    closes = [b.close for b in bars[-20:]]
    ma = sum(closes, Decimal("0")) / Decimal(20)
    return bars[-1].close > ma


def _index_alignment(md, end: datetime) -> tuple[bool | None, bool | None]:
    """Gap 7: real SPY/QQQ vs 20-DMA (None if bars thin)."""
    start = end - timedelta(days=40)
    spy_ok: bool | None = None
    qqq_ok: bool | None = None
    try:
        spy = md.get_bars(BarRequest(symbol="SPY", timeframe="1Day", start=start, end=end))
        spy_ok = _above_20dma(spy)
    except Exception:  # noqa: BLE001
        spy_ok = None
    try:
        qqq = md.get_bars(BarRequest(symbol="QQQ", timeframe="1Day", start=start, end=end))
        qqq_ok = _above_20dma(qqq)
    except Exception:  # noqa: BLE001
        qqq_ok = None
    return spy_ok, qqq_ok


# Liquid mega names without a cap API → large_cap route.
LARGE_CAP_SYMBOLS = frozenset(
    {
        "SPY",
        "QQQ",
        "IWM",
        "DIA",
        "AAPL",
        "MSFT",
        "NVDA",
        "AMZN",
        "GOOGL",
        "GOOG",
        "META",
        "TSLA",
        # Frequent watchlist names that were mis-routed speculative on Finnhub miss.
        "INTC",
        "PLTR",
        "NOK",
        "SOFI",
        "AMD",
        "MU",
        "UBER",
        "COIN",
        "HOOD",
    }
)

# Known $2B–$10B names → mid_cap route when Finnhub cap is unavailable.
MID_CAP_SYMBOLS = frozenset(
    {
        "AAL",
        "SNAP",
        "RBLX",
        "PLUG",
        "LCID",
    }
)


def resolve_sniper_agent(market_cap_usd: Decimal | None, symbol: str) -> str:
    """Return large_cap_sniper | mid_cap_sniper | speculative_sniper."""
    sym = symbol.upper()
    if sym in LARGE_CAP_SYMBOLS:
        return LARGE_CAP_SNIPER.agent_id
    if sym in MID_CAP_SYMBOLS:
        return MID_CAP_SNIPER.agent_id
    if market_cap_usd is None:
        # Screener watchlist default → speculative
        return SPECULATIVE_SNIPER.agent_id
    if market_cap_usd < MID_CAP_SNIPER.min_market_cap_usd:
        return SPECULATIVE_SNIPER.agent_id
    if market_cap_usd < MID_CAP_SNIPER.max_market_cap_usd:
        return MID_CAP_SNIPER.agent_id
    return LARGE_CAP_SNIPER.agent_id


def resolve_market_cap(symbol: str, explicit: Decimal | None = None) -> Decimal | None:
    """Explicit → static-list heuristic → Finnhub profile2 (millions→USD)."""
    if explicit is not None:
        return explicit
    if symbol.upper() in LARGE_CAP_SYMBOLS:
        return Decimal("3000000000000")
    if symbol.upper() in MID_CAP_SYMBOLS:
        return Decimal("5000000000")
    return fetch_market_cap_usd(symbol)


def _max_entries_per_symbol_day() -> int:
    try:
        return max(1, int(os.environ.get("MAX_ENTRIES_PER_SYMBOL_DAY", "1")))
    except ValueError:
        return 1


def _spec_last_entry_et() -> time:
    raw = os.environ.get("SPEC_LAST_ENTRY_ET", "15:00")
    try:
        hh, mm = raw.split(":", 1)
        return time(int(hh), int(mm))
    except (ValueError, AttributeError):
        return time(15, 0)


def _watchlist_day_change(symbol: str) -> Decimal | None:
    """Screener percent_change from latest watchlist candidate, if present."""
    try:
        doc = get_watchlist(refresh=False)
    except Exception:  # noqa: BLE001
        return None
    sym = symbol.upper()
    for cand in doc.candidates:
        if cand.symbol.upper() == sym and cand.percent_change not in (None, ""):
            try:
                return Decimal(str(cand.percent_change))
            except Exception:  # noqa: BLE001
                return None
    return None


def run_sniper_paper_tick(
    *,
    symbol: str,
    journal_path: str,
    agent_id: str,
    market_cap_usd: Decimal | None,
    broker: AlpacaPaperBroker | None = None,
    notional_usd: Decimal | None = None,
) -> dict:
    """1Min bars → routed sniper eval → paper bracket on ENTER."""
    run_id = uuid4()
    end = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    start = end - timedelta(hours=2)
    md = resolve_market_data()
    bars = md.get_bars(
        BarRequest(
            symbol=symbol,
            timeframe="1Min",
            start=start,
            end=end,
            feed=V1_VENDORS.alpaca_feed,
        )
    )
    journal = SqliteJournal(journal_path)
    if len(bars) < 21:
        write_skip(
            journal,
            run_id=run_id,
            agent=agent_id,
            symbol=symbol,
            ts=end,
            skip_reason=SkipReason.INSUFFICIENT_BARS,
            detail=f"insufficient_bars={len(bars)}",
            meta={"bars": len(bars)},
        )
        return {
            "symbol": symbol,
            "mode": "paper",
            "status": "NO_TRADE",
            "found_by_agent": agent_id,
            "detail": f"insufficient_bars={len(bars)}",
            "orders": 0,
            "skips": 1,
        }

    broker = broker or AlpacaPaperBroker()
    risk, equity, use_notional = make_risk_gate(
        broker, journal_path=journal_path, notional_usd=notional_usd
    )
    # Speculative: half-slice (equity/10) unless caller overrides notional_usd.
    if notional_usd is None:
        use_notional = agent_slice_notional(equity, agent_id)

    if broker.has_open_position(symbol):
        write_skip(
            journal,
            run_id=run_id,
            agent=agent_id,
            symbol=symbol,
            ts=bars[-1].ts,
            skip_reason=SkipReason.MAX_POSITIONS,
            detail="already_open_on_alpaca_paper",
            meta={"equity": str(equity)},
        )
        return {
            "symbol": symbol,
            "mode": "paper",
            "status": "SKIP",
            "found_by_agent": agent_id,
            "detail": "already_open",
            "equity": str(equity),
            "orders": 0,
            "skips": 1,
        }

    # F2: one ENTER per symbol per ET session day per agent (ENSC 3x, ZYBT 6x).
    day_start_et = now_et().replace(hour=0, minute=0, second=0, microsecond=0)
    entries_today = journal.count_symbol_entries_since(
        symbol, agent_id, day_start_et.astimezone(timezone.utc).isoformat()
    )
    if entries_today >= _max_entries_per_symbol_day():
        write_skip(
            journal,
            run_id=run_id,
            agent=agent_id,
            symbol=symbol,
            ts=bars[-1].ts,
            skip_reason=SkipReason.RISK_BLOCKED,
            detail=f"repeat_entry_symbol_day entries_today={entries_today}",
        )
        return {
            "symbol": symbol,
            "mode": "paper",
            "status": "SKIP",
            "found_by_agent": agent_id,
            "detail": "repeat_entry_symbol_day",
            "orders": 0,
            "skips": 1,
        }

    # F9: no fresh speculative entries late in the session (intraday, flat by EOD).
    if agent_id == SPECULATIVE_SNIPER.agent_id and now_et().time() >= _spec_last_entry_et():
        write_skip(
            journal,
            run_id=run_id,
            agent=agent_id,
            symbol=symbol,
            ts=bars[-1].ts,
            skip_reason=SkipReason.OUTSIDE_WINDOW,
            detail=f"speculative_late_first_entry et={now_et().time()}",
        )
        return {
            "symbol": symbol,
            "mode": "paper",
            "status": "SKIP",
            "found_by_agent": agent_id,
            "detail": "speculative_late_first_entry",
            "orders": 0,
            "skips": 1,
        }

    # 2026-08-05: block leveraged / ETF products that slipped past watchlist.
    if is_disallowed_product(symbol):
        write_skip(
            journal,
            run_id=run_id,
            agent=agent_id,
            symbol=symbol,
            ts=bars[-1].ts,
            skip_reason=SkipReason.MARKET_GUARDRAIL,
            detail="disallowed_product",
        )
        return {
            "symbol": symbol,
            "mode": "paper",
            "status": "SKIP",
            "found_by_agent": agent_id,
            "detail": "disallowed_product",
            "orders": 0,
            "skips": 1,
        }

    # Speculative chase guard — tighter than book-wide 40% (default 25%).
    if agent_id == SPECULATIVE_SNIPER.agent_id:
        pct = _watchlist_day_change(symbol)
        if speculative_day_gain_too_extended(pct):
            write_skip(
                journal,
                run_id=run_id,
                agent=agent_id,
                symbol=symbol,
                ts=bars[-1].ts,
                skip_reason=SkipReason.MARKET_GUARDRAIL,
                detail=f"spec_day_gain_extended pct={pct}",
            )
            return {
                "symbol": symbol,
                "mode": "paper",
                "status": "SKIP",
                "found_by_agent": agent_id,
                "detail": "spec_day_gain_extended",
                "orders": 0,
                "skips": 1,
            }

    # 2026-08-05: limit concurrent large_cap opens (correlated morning cluster).
    if agent_id == LARGE_CAP_SNIPER.agent_id:
        open_large = sum(
            1
            for plan in load_open_plans(journal_path).values()
            if plan.get("found_by_agent") == LARGE_CAP_SNIPER.agent_id
        )
        if open_large >= max_open_large_cap():
            write_skip(
                journal,
                run_id=run_id,
                agent=agent_id,
                symbol=symbol,
                ts=bars[-1].ts,
                skip_reason=SkipReason.MAX_POSITIONS,
                detail=f"max_open_large_cap open={open_large}",
            )
            return {
                "symbol": symbol,
                "mode": "paper",
                "status": "SKIP",
                "found_by_agent": agent_id,
                "detail": "max_open_large_cap",
                "orders": 0,
                "skips": 1,
            }

    bar = bars[-1]
    spy_aligned, qqq_aligned = _index_alignment(md, end)
    has_catalyst = _paper_has_catalyst(symbol)
    ctx = SessionContext(
        symbol=symbol,
        bar=bar,
        bars=bars,
        market_cap_usd=market_cap_usd,
        has_catalyst=has_catalyst,
        spy_aligned=spy_aligned,
        qqq_aligned=qqq_aligned,
    )
    if agent_id == SPECULATIVE_SNIPER.agent_id:
        decision = evaluate_speculative_sniper(ctx, mode=RunMode.PAPER)
    elif agent_id == MID_CAP_SNIPER.agent_id:
        decision = evaluate_mid_cap_sniper(ctx, mode=RunMode.PAPER)
    else:
        decision = evaluate_large_cap_sniper(ctx, mode=RunMode.PAPER)

    if decision.status != SniperStatus.ENTER or decision.trade_map is None:
        write_skip(
            journal,
            run_id=run_id,
            agent=decision.agent_id,
            symbol=symbol,
            ts=bar.ts,
            skip_reason=SkipReason.SETUP_MISSING,
            detail=decision.reason or decision.status.value,
            meta={"sniper_status": decision.status.value},
        )
        return {
            "symbol": symbol,
            "mode": "paper",
            "status": decision.status.value,
            "found_by_agent": decision.agent_id,
            "detail": decision.reason,
            "equity": str(equity),
            "orders": 0,
            "skips": 1,
        }

    qty = qty_for_price(decision.trade_map.entry_trigger, use_notional)
    if qty is None:
        write_skip(
            journal,
            run_id=run_id,
            agent=decision.agent_id,
            symbol=symbol,
            ts=bar.ts,
            skip_reason=SkipReason.RISK_BLOCKED,
            detail=(
                f"slice_cannot_buy_1_share price={decision.trade_map.entry_trigger} "
                f"notional={use_notional}"
            ),
        )
        return {
            "symbol": symbol,
            "mode": "paper",
            "status": "RISK_BLOCKED",
            "found_by_agent": decision.agent_id,
            "detail": "qty_unaffordable",
            "equity": str(equity),
            "orders": 0,
            "skips": 1,
        }
    intent = decision.to_trade_intent(qty)
    assert intent is not None
    return submit_paper_intent(
        broker=broker,
        journal=journal,
        risk=risk,
        intent=intent,
        run_id=run_id,
        bar_ts=bar.ts,
        equity=equity,
        notional_usd=use_notional,
        journal_path=journal_path,
    )


def run_symbol_paper_tick(
    *,
    symbol: str,
    journal_path: str,
    market_cap_usd: Decimal | None = None,
    notional_usd: Decimal | None = None,
) -> dict:
    """Route sniper by cap + evaluate swing (orders only in power hour)."""
    cap = resolve_market_cap(symbol, market_cap_usd)
    sniper_id = resolve_sniper_agent(cap, symbol)
    broker = AlpacaPaperBroker()
    results: dict = {
        "symbol": symbol,
        "market_cap_usd": str(cap) if cap is not None else None,
        "sniper_agent": sniper_id,
        "swing_power_hour": swing_power_hour(),
        "orders": 0,
        "skips": 0,
    }

    sniper_out = run_sniper_paper_tick(
        symbol=symbol,
        journal_path=journal_path,
        agent_id=sniper_id,
        market_cap_usd=cap,
        broker=broker,
        notional_usd=notional_usd,
    )
    results["sniper"] = sniper_out
    results["orders"] += int(sniper_out.get("orders") or 0)
    results["skips"] += int(sniper_out.get("skips") or 0)
    results["found_by_agent"] = sniper_out.get("found_by_agent")
    results["status"] = sniper_out.get("status")

    swing_out = run_swing_paper_tick(
        symbol=symbol,
        journal_path=journal_path,
        market_cap_usd=cap,
        broker=broker,
        notional_usd=notional_usd,
        submit=swing_power_hour(),
    )
    results["swing"] = swing_out
    results["orders"] += int(swing_out.get("orders") or 0)
    results["skips"] += int(swing_out.get("skips") or 0)
    if swing_out.get("status") == "ORDER_SUBMITTED":
        results["found_by_agent"] = swing_out.get("found_by_agent")
        results["status"] = "ORDER_SUBMITTED"
    elif "status" not in results:
        results["status"] = swing_out.get("status", "NO_TRADE")

    logger.info(
        "symbol_tick %s sniper=%s swing=%s orders=%s",
        symbol,
        sniper_id,
        swing_out.get("status"),
        results["orders"],
    )
    return results

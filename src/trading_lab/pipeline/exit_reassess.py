"""Daily exit reassessment for open paper positions.

Enforces: never sit naked; agent-aware scale ladders; orphan flatten;
swing 8-EMA + max-hold; snipers never overnight via GTC rearm; trail after scale.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from decimal import ROUND_DOWN, Decimal
from enum import StrEnum
from typing import Any

from trading_lab.agents.sniper.shared_execution import SNIPER_SHARED, scale_out_price
from trading_lab.agents.swing.shared_execution import SWING_SHARED
from trading_lab.broker.alpaca import AlpacaPaperBroker
from trading_lab.config.secrets import has_alpaca_keys
from trading_lab.eval.swing import _ema
from trading_lab.execution.risk_persist import load_risk_gate, save_risk_gate
from trading_lab.journal.open_trades import (
    close_journal_trade,
    load_open_plans,
    mark_journal_scaled,
    update_last_mark,
    update_trail_stop,
)
from trading_lab.market_data.factory import resolve_market_data
from trading_lab.market_data.types import BarRequest
from trading_lab.schemas.trades import ExitReason

logger = logging.getLogger("trading_lab.exit_reassess")

SNIPER_AGENTS = frozenset(
    {"large_cap_sniper", "mid_cap_sniper", "speculative_sniper", "gainer_sniper"}
)


class ExitAction(StrEnum):
    FLATTEN_TARGET = "flatten_target"
    FLATTEN_STOP = "flatten_stop"
    FLATTEN_ORPHAN = "flatten_orphan"
    FLATTEN_EMA = "flatten_ema"
    FLATTEN_TIME = "flatten_time"
    FLATTEN_SNIPER_EOD = "flatten_sniper_eod"
    SCALE_AND_TRAIL = "scale_and_trail"
    TRAIL_UPDATE = "trail_update"
    REARM_OCO = "rearm_oco"
    NOOP_HAS_EXITS = "noop_has_exits"
    SKIP_NO_PLAN = "skip_no_plan"  # legacy alias → orphan flatten


def assess_exit_action(
    *,
    entry: Decimal,
    mark: Decimal,
    stop: Decimal,
    target: Decimal,
    scale_px: Decimal | None = None,
    scale_gain_pct: Decimal = SWING_SHARED.scale_out_gain_pct,
) -> ExitAction:
    """Pure ladder: stop → rearm → scale → harvest past final target."""
    if mark <= stop:
        return ExitAction.FLATTEN_STOP
    if mark >= target:
        return ExitAction.FLATTEN_TARGET
    scale = (
        scale_px
        if scale_px is not None
        else entry * (Decimal("1") + scale_gain_pct / Decimal("100"))
    )
    if mark >= scale:
        return ExitAction.SCALE_AND_TRAIL
    return ExitAction.REARM_OCO


def _half_qty(qty: Decimal) -> Decimal:
    half = (qty * Decimal("0.5")).to_integral_value(rounding=ROUND_DOWN)
    return half if half >= 1 else Decimal("1")


def _has_sell_exit(orders: list[dict[str, Any]], symbol: str) -> bool:
    sym = symbol.upper()
    for o in orders:
        if str(o.get("symbol") or "").upper() != sym:
            continue
        if str(o.get("side") or "").lower() != "sell":
            continue
        status = str(o.get("status") or "").lower()
        if status in {"new", "accepted", "pending_new", "held", "partially_filled"}:
            return True
    return False


def _is_sniper(agent: str) -> bool:
    return agent in SNIPER_AGENTS


def _close_position_retry(
    broker: AlpacaPaperBroker,
    symbol: str,
    *,
    qty: Decimal | None = None,
    attempts: int = 3,
    wait_sec: float = 1.0,
) -> dict:
    """Close with cancel+retry when shares are held_for_orders by bracket legs.

    2026-08-04: reassess hit 403 'insufficient qty available' on AAPL/NVDA —
    the sell raced still-open exit legs. Cancel and retry instead of failing.
    """
    import time

    last: Exception | None = None
    for _ in range(attempts):
        try:
            if qty is None:
                return broker.close_position(symbol)
            return broker.close_position(symbol, qty=qty)
        except Exception as exc:  # noqa: BLE001
            last = exc
            if "insufficient qty" not in str(exc):
                raise
            broker.cancel_open_orders(symbol)
            time.sleep(wait_sec)
    assert last is not None
    raise last


def _sessions_held(entry_ts: str | None, now: datetime) -> int:
    if not entry_ts:
        return 0
    try:
        start = datetime.fromisoformat(entry_ts)
    except ValueError:
        return 0
    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    held = now.astimezone(timezone.utc).date() - start.astimezone(timezone.utc).date()
    return max(0, held.days)


def _below_8ema(symbol: str) -> bool | None:
    """True if latest daily close is below 8-EMA."""
    try:
        md = resolve_market_data()
        end = datetime.now(timezone.utc)
        start = end.replace(hour=0, minute=0, second=0, microsecond=0)
        # ~40 calendar days of dailies
        from datetime import timedelta

        bars = md.get_bars(
            BarRequest(
                symbol=symbol,
                timeframe="1Day",
                start=start - timedelta(days=40),
                end=end,
            )
        )
    except Exception:  # noqa: BLE001
        return None
    if len(bars) < 8:
        return None
    closes = [b.close for b in bars]
    ema = _ema(closes, 8)
    if ema is None:
        return None
    return bars[-1].close < ema


def _trail_stop(
    entry: Decimal,
    mark: Decimal,
    prev_trail: Decimal | None,
    *,
    recent_lows: list[Decimal] | None = None,
    atr: Decimal | None = None,
) -> Decimal:
    """Ratchet stop: max(BE, swing-low, mark−1.5·ATR, prior); always < mark."""
    candidates: list[Decimal] = [entry]
    if recent_lows:
        candidates.append(min(recent_lows))
    if atr is not None and atr > 0:
        candidates.append((mark - atr * Decimal("1.5")).quantize(Decimal("0.01")))
    else:
        candidates.append((mark * Decimal("0.98")).quantize(Decimal("0.01")))
    candidate = max(candidates)
    if prev_trail is not None:
        candidate = max(candidate, prev_trail)
    if candidate >= mark:
        candidate = max(entry, (mark * Decimal("0.995")).quantize(Decimal("0.01")))
        if candidate >= mark:
            candidate = (mark - Decimal("0.01")).quantize(Decimal("0.01"))
    return candidate.quantize(Decimal("0.01"))


def _recent_bar_stats(symbol: str) -> tuple[list[Decimal], Decimal | None]:
    """Return (recent lows, ATR proxy) from 1Min bars when available."""
    try:
        md = resolve_market_data()
        end = datetime.now(timezone.utc)
        from datetime import timedelta

        bars = md.get_bars(
            BarRequest(
                symbol=symbol,
                timeframe="1Min",
                start=end - timedelta(hours=2),
                end=end,
            )
        )
    except Exception:  # noqa: BLE001
        return [], None
    if len(bars) < 5:
        return [], None
    window = bars[-20:] if len(bars) >= 20 else bars
    lows = [b.low for b in window]
    ranges = [b.high - b.low for b in window if b.high >= b.low]
    atr = None
    if ranges:
        atr = (sum(ranges, Decimal("0")) / Decimal(len(ranges))).quantize(Decimal("0.01"))
    return lows[-5:], atr


def _resolve_exit_px(
    *,
    broker: AlpacaPaperBroker,
    symbol: str,
    plan: dict[str, Any],
    entry: Decimal,
) -> Decimal:
    """Prefer broker sell fill → last_mark → stop/target heuristics → entry."""
    fill = None
    if hasattr(broker, "recent_sell_fill_price"):
        try:
            fill = broker.recent_sell_fill_price(symbol)
        except Exception:  # noqa: BLE001
            fill = None
    if fill is not None and fill > 0:
        return fill
    meta = plan.get("meta") or {}
    last = meta.get("last_mark")
    if last not in (None, ""):
        try:
            px = Decimal(str(last))
            if px > 0:
                return px
        except Exception:  # noqa: BLE001
            pass
    return entry


def _scale_point_for_plan(plan: dict[str, Any], entry: Decimal, target: Decimal) -> Decimal:
    if plan.get("scale_out_point"):
        return Decimal(str(plan["scale_out_point"]))
    agent = str(plan.get("found_by_agent") or "")
    if _is_sniper(agent):
        return scale_out_price(entry, target)
    return entry * (Decimal("1") + SWING_SHARED.scale_out_gain_pct / Decimal("100"))


def reconcile_flat_journal(
    journal_path: str,
    *,
    broker: AlpacaPaperBroker,
) -> list[dict[str, Any]]:
    """Close journal opens whose broker position is gone; book P&L + risk."""
    plans = load_open_plans(journal_path)
    open_syms = {p.symbol.upper() for p in broker.get_open_positions() if p.qty != 0}
    out: list[dict[str, Any]] = []
    gate = load_risk_gate(journal_path)
    now = datetime.now(timezone.utc)
    for sym, plan in plans.items():
        if sym in open_syms:
            continue
        entry = plan["entry_px"]
        exit_px = _resolve_exit_px(broker=broker, symbol=sym, plan=plan, entry=entry)
        reason = ExitReason.SIGNAL
        stop_hit = exit_px <= plan["stop_px"] if plan["stop_px"] > 0 else False
        if stop_hit:
            reason = ExitReason.STOP
        elif plan["target_px"] > 0 and exit_px >= plan["target_px"]:
            reason = ExitReason.TARGET
        pnl = (exit_px - entry) * plan["qty"]
        close_journal_trade(
            journal_path,
            sym,
            exit_px=exit_px,
            exit_reason=reason,
            closed_by="reconcile",
            trade_id=plan.get("trade_id"),
        )
        gate.on_close(
            pnl,
            stop_hit=stop_hit,
            now=now,
            cool_minutes=int(SNIPER_SHARED.cooling_off_after_stop.total_seconds() // 60),
        )
        out.append(
            {
                "ok": True,
                "symbol": sym,
                "action": "reconcile_closed",
                "exit_px": str(exit_px),
                "pnl_usd": str(pnl),
                "exit_reason": reason.value,
            }
        )
    save_risk_gate(journal_path, gate)
    return out


def reassess_open_exits(
    journal_path: str,
    *,
    broker: AlpacaPaperBroker | None = None,
    outside_rth: bool | None = None,
) -> list[dict[str, Any]]:
    """Assess every open broker position and enforce an exit plan."""
    if broker is None:
        if not has_alpaca_keys():
            return [{"ok": False, "detail": "no_alpaca_keys"}]
        broker = AlpacaPaperBroker()

    out: list[dict[str, Any]] = []
    out.extend(reconcile_flat_journal(journal_path, broker=broker))

    plans = load_open_plans(journal_path)
    positions = broker.get_open_positions()
    if not positions:
        if not out:
            return [{"ok": True, "detail": "no_open_positions"}]
        return out

    from trading_lab.schedule.market_clock import sniper_ticks_allowed

    if outside_rth is None:
        outside_rth = not sniper_ticks_allowed()

    open_orders = broker.list_open_orders()
    gate = load_risk_gate(journal_path)
    now = datetime.now(timezone.utc)

    for pos in positions:
        sym = pos.symbol.upper()
        if pos.qty <= 0:
            continue
        plan = plans.get(sym)
        mark = pos.current_price if pos.current_price > 0 else pos.avg_entry_price
        if mark > 0 and plan is not None:
            update_last_mark(journal_path, sym, mark)
        entry = (
            pos.avg_entry_price
            if pos.avg_entry_price > 0
            else (plan["entry_px"] if plan else Decimal("0"))
        )

        # Gap 4: orphan → flatten
        if plan is None or entry <= 0 or plan["stop_px"] <= 0 or plan["target_px"] <= 0:
            try:
                broker.cancel_open_orders(sym)
                _close_position_retry(broker, sym)
                close_journal_trade(
                    journal_path,
                    sym,
                    exit_px=mark if mark > 0 else entry,
                    exit_reason=ExitReason.RISK_KILL,
                    closed_by="orphan_flatten",
                    trade_id=(plan or {}).get("trade_id"),
                )
                out.append(
                    {
                        "ok": True,
                        "symbol": sym,
                        "action": ExitAction.FLATTEN_ORPHAN.value,
                        "detail": "open_on_broker_without_journal_exit_plan",
                        "mark": str(mark),
                    }
                )
            except Exception as exc:  # noqa: BLE001
                out.append(
                    {
                        "ok": False,
                        "symbol": sym,
                        "action": ExitAction.FLATTEN_ORPHAN.value,
                        "detail": str(exc),
                    }
                )
            continue

        stop = plan["stop_px"]
        target = plan["target_px"]
        agent = str(plan.get("found_by_agent") or "")
        meta = plan.get("meta") or {}
        scale_px = _scale_point_for_plan(plan, entry, target)
        has_exits = _has_sell_exit(open_orders, sym)
        scaled = bool(meta.get("scaled_out"))
        hold = plan.get("hold_plan") or {}
        max_sessions = int(hold.get("max_hold_sessions") or 0)

        # Gap 5: max hold / 8-EMA (swing)
        if not _is_sniper(agent):
            held = _sessions_held(plan.get("entry_ts"), now)
            if max_sessions > 0 and held >= max_sessions:
                try:
                    if has_exits:
                        broker.cancel_open_orders(sym)
                    _close_position_retry(broker, sym)
                    pnl = (mark - entry) * pos.qty
                    close_journal_trade(
                        journal_path,
                        sym,
                        exit_px=mark,
                        exit_reason=ExitReason.TIME,
                        trade_id=plan.get("trade_id"),
                    )
                    gate.on_close(pnl, stop_hit=False, now=now)
                    out.append(
                        {
                            "ok": True,
                            "symbol": sym,
                            "action": ExitAction.FLATTEN_TIME.value,
                            "sessions_held": held,
                        }
                    )
                except Exception as exc:  # noqa: BLE001
                    out.append(
                        {"ok": False, "symbol": sym, "action": "flatten_time", "detail": str(exc)}
                    )
                continue
            if SWING_SHARED.exit_on_close_below_8ema:
                below = _below_8ema(sym)
                if below is True:
                    try:
                        if has_exits:
                            broker.cancel_open_orders(sym)
                        _close_position_retry(broker, sym)
                        pnl = (mark - entry) * pos.qty
                        close_journal_trade(
                            journal_path,
                            sym,
                            exit_px=mark,
                            exit_reason=ExitReason.EMA_BREAK,
                            trade_id=plan.get("trade_id"),
                        )
                        gate.on_close(pnl, stop_hit=False, now=now)
                        out.append(
                            {
                                "ok": True,
                                "symbol": sym,
                                "action": ExitAction.FLATTEN_EMA.value,
                                "mark": str(mark),
                            }
                        )
                    except Exception as exc:  # noqa: BLE001
                        out.append(
                            {
                                "ok": False,
                                "symbol": sym,
                                "action": "flatten_ema",
                                "detail": str(exc),
                            }
                        )
                    continue

        # Gap 8: sniper outside RTH → flatten, never GTC rearm
        if _is_sniper(agent) and outside_rth:
            try:
                if has_exits:
                    broker.cancel_open_orders(sym)
                _close_position_retry(broker, sym)
                pnl = (mark - entry) * pos.qty
                close_journal_trade(
                    journal_path,
                    sym,
                    exit_px=mark,
                    exit_reason=ExitReason.EOD,
                    trade_id=plan.get("trade_id"),
                )
                gate.on_close(pnl, stop_hit=False, now=now)
                out.append(
                    {
                        "ok": True,
                        "symbol": sym,
                        "action": ExitAction.FLATTEN_SNIPER_EOD.value,
                        "mark": str(mark),
                    }
                )
            except Exception as exc:  # noqa: BLE001
                out.append(
                    {
                        "ok": False,
                        "symbol": sym,
                        "action": "flatten_sniper_eod",
                        "detail": str(exc),
                    }
                )
            continue

        action = assess_exit_action(
            entry=entry, mark=mark, stop=stop, target=target, scale_px=scale_px
        )
        tif = "day" if _is_sniper(agent) else "gtc"

        try:
            if action in {ExitAction.FLATTEN_TARGET, ExitAction.FLATTEN_STOP}:
                if has_exits:
                    broker.cancel_open_orders(sym)
                _close_position_retry(broker, sym)
                pnl = (mark - entry) * pos.qty
                reason = (
                    ExitReason.TARGET if action == ExitAction.FLATTEN_TARGET else ExitReason.STOP
                )
                close_journal_trade(
                    journal_path,
                    sym,
                    exit_px=mark,
                    exit_reason=reason,
                    trade_id=plan.get("trade_id"),
                )
                gate.on_close(
                    pnl,
                    stop_hit=(action == ExitAction.FLATTEN_STOP),
                    now=now,
                    cool_minutes=int(SNIPER_SHARED.cooling_off_after_stop.total_seconds() // 60),
                )
                out.append(
                    {
                        "ok": True,
                        "symbol": sym,
                        "action": action.value,
                        "mark": str(mark),
                        "target": str(target),
                        "stop": str(stop),
                    }
                )
                continue

            if action == ExitAction.SCALE_AND_TRAIL:
                remain = pos.qty
                prev_trail = Decimal(str(meta["trail_stop"])) if meta.get("trail_stop") else None
                lows, atr = _recent_bar_stats(sym)
                if not scaled and pos.qty >= 2:
                    sell_qty = _half_qty(pos.qty)
                    broker.cancel_open_orders(sym)
                    _close_position_retry(broker, sym, qty=sell_qty)
                    remain = pos.qty - sell_qty
                    if remain < 1:
                        remain = Decimal("1")
                    trail = _trail_stop(entry, mark, prev_trail, recent_lows=lows, atr=atr)
                    mark_journal_scaled(journal_path, sym, remain, trail_stop=trail)
                    has_exits = False
                    scaled = True
                    prev_trail = trail
                trail = _trail_stop(entry, mark, prev_trail, recent_lows=lows, atr=atr)
                # Gap 10: if already scaled and trail moved up, replace OCO
                if scaled and has_exits and prev_trail is not None and trail > prev_trail:
                    broker.cancel_open_orders(sym)
                    has_exits = False
                    update_trail_stop(journal_path, sym, trail)
                    action_out = ExitAction.TRAIL_UPDATE
                else:
                    action_out = ExitAction.SCALE_AND_TRAIL
                    update_trail_stop(journal_path, sym, trail)
                if not has_exits:
                    broker.submit_oco_exit(
                        symbol=sym,
                        qty=remain,
                        stop_px=trail,
                        target_px=target,
                        time_in_force=tif,
                    )
                out.append(
                    {
                        "ok": True,
                        "symbol": sym,
                        "action": action_out.value,
                        "mark": str(mark),
                        "remain_qty": str(remain),
                        "stop": str(trail),
                        "target": str(target),
                        "scaled": True,
                    }
                )
                continue

            # REARM_OCO
            if has_exits:
                out.append(
                    {
                        "ok": True,
                        "symbol": sym,
                        "action": ExitAction.NOOP_HAS_EXITS.value,
                        "mark": str(mark),
                    }
                )
                continue
            broker.submit_oco_exit(
                symbol=sym,
                qty=pos.qty,
                stop_px=stop,
                target_px=target,
                time_in_force=tif,
            )
            out.append(
                {
                    "ok": True,
                    "symbol": sym,
                    "action": ExitAction.REARM_OCO.value,
                    "mark": str(mark),
                    "stop": str(stop),
                    "target": str(target),
                    "qty": str(pos.qty),
                    "tif": tif,
                }
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("exit reassess failed for %s", sym)
            out.append({"ok": False, "symbol": sym, "action": action.value, "detail": str(exc)})

    save_risk_gate(journal_path, gate)
    return out

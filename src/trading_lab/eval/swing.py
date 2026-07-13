"""Swing momentum evaluator — hard gates + Unusual Whales congress soft overlay.

Congress / flow never upgrades NO_TRADE → ENTER (never_force_trade).
"""

from __future__ import annotations

from decimal import Decimal

from trading_lab.agents.swing.decision import SwingDecision, SwingStatus, SwingTradeMap
from trading_lab.agents.swing.momentum import (
    SWING_MOMENTUM,
    CapTier,
    SwingMomentumSpec,
)
from trading_lab.agents.swing.shared_execution import SWING_SHARED
from trading_lab.catalysts.types import CatalystKind, CatalystSignal
from trading_lab.market_data.types import Bar, SessionContext
from trading_lab.schemas.trades import Side


def _rvol(bars: list[Bar], lookback: int = 20) -> Decimal | None:
    if len(bars) < lookback + 1:
        return None
    last = bars[-1].volume
    avg = sum((b.volume for b in bars[-(lookback + 1) : -1]), Decimal("0")) / Decimal(lookback)
    if avg == 0:
        return None
    return last / avg


def _ema(closes: list[Decimal], period: int) -> Decimal | None:
    if len(closes) < period:
        return None
    k = Decimal("2") / Decimal(period + 1)
    ema = sum(closes[:period], Decimal("0")) / Decimal(period)
    for price in closes[period:]:
        ema = price * k + ema * (Decimal("1") - k)
    return ema


def _cap_tier(spec: SwingMomentumSpec, market_cap_usd: Decimal | None) -> CapTier:
    if market_cap_usd is None:
        return CapTier.MID
    if market_cap_usd >= spec.large_cap_min_usd:
        return CapTier.LARGE
    if market_cap_usd >= spec.mid_cap_min_usd:
        return CapTier.MID
    return CapTier.MICRO


def _congress_flags(
    signals: list[CatalystSignal],
) -> tuple[bool, bool, list[str]]:
    """Return (has_buy, has_sell, tags)."""
    has_buy = False
    has_sell = False
    tags: list[str] = []
    for s in signals:
        if s.kind != CatalystKind.CONGRESS_TRADE:
            continue
        if s.direction == "buy":
            has_buy = True
            tags.append(f"congress_buy:{s.politician or 'unknown'}")
        elif s.direction == "sell":
            has_sell = True
            tags.append(f"congress_sell:{s.politician or 'unknown'}")
    return has_buy, has_sell, tags


def apply_congress_soft_overlay(
    status: SwingStatus,
    *,
    signals: list[CatalystSignal],
    enabled: bool,
    soft_only: bool,
    meta: dict,
) -> tuple[SwingStatus, str, dict]:
    """Skip or raise priority only — never create ENTER from NO_TRADE/WATCH."""
    if not enabled:
        return status, "", meta

    has_buy, has_sell, tags = _congress_flags(signals)
    if not has_buy and not has_sell:
        return status, "", meta

    meta = {
        **meta,
        "congress_tags": tags,
        "priority": int(meta.get("priority", 0)),
    }
    note = ""

    if has_sell and status == SwingStatus.ENTER:
        # Soft skip: do not enter against recent disclosed congressional sells
        status = SwingStatus.WATCH
        note = "congress_sell_soft_skip"
        meta["congress_action"] = "soft_skip_sell"
    elif has_buy and status in {SwingStatus.ENTER, SwingStatus.WATCH}:
        meta["priority"] = int(meta["priority"]) + 1
        meta["congress_action"] = "priority_boost_buy"
        note = "congress_buy_priority"
    elif has_buy and status == SwingStatus.NO_TRADE:
        # Soft only: never upgrade to ENTER
        meta["congress_action"] = "ignored_no_setup"
        note = "congress_buy_no_force"
        if not soft_only:
            # Even if soft_only=False, still refuse force-enter (invariant)
            meta["congress_action"] = "blocked_force_enter"

    return status, note, meta


def evaluate_swing_momentum(
    ctx: SessionContext,
    *,
    spec: SwingMomentumSpec = SWING_MOMENTUM,
) -> SwingDecision:
    """Return ENTER / WATCH / NO_TRADE. Never forces a trade."""
    agent = spec.agent_id
    symbol = ctx.symbol
    bar = ctx.bar
    tier = _cap_tier(spec, ctx.market_cap_usd)
    rvol = ctx.rvol if ctx.rvol is not None else _rvol(ctx.bars)

    closes = [b.close for b in ctx.bars] if ctx.bars else [bar.close]
    ema8 = _ema(closes, 8)
    above_8ema = ctx.price_above_8ema
    if above_8ema is None and ema8 is not None:
        above_8ema = bar.close > ema8

    trend_ok = ctx.spy_or_qqq_above_20dma
    if trend_ok is None:
        # Fall back to sniper-style alignment flags when DMA not supplied
        spy_ok = True if ctx.spy_aligned is None else ctx.spy_aligned
        qqq_ok = True if ctx.qqq_aligned is None else ctx.qqq_aligned
        trend_ok = spy_ok or qqq_ok

    rvol_min = (
        spec.rvol_gate(ctx.market_cap_usd) if ctx.market_cap_usd is not None else spec.mid_rvol_min
    )
    rs_ok = True
    if tier == CapTier.MID and spec.mid_require_rs_vs_spy_qqq:
        rs_ok = True if ctx.rs_vs_spy_qqq is None else ctx.rs_vs_spy_qqq

    missing: list[str] = []
    if spec.require_spy_or_qqq_above_20dma and not trend_ok:
        missing.append("spy_qqq_below_20dma")
    if spec.require_price_above_8ema and above_8ema is False:
        missing.append("below_8ema")
    if rvol is None or rvol < rvol_min:
        missing.append(f"rvol<{rvol_min}")
    if not rs_ok:
        missing.append("rs_vs_spy_qqq")

    volume_analysis = f"rvol={rvol}; tier={tier.value}; rvol_min={rvol_min}"
    meta: dict = {
        "found_by_agent": agent,
        "missing_gates": missing,
        "priority": 0,
        "cap_tier": tier.value,
    }

    if missing:
        soft = {"below_8ema"}
        status = (
            SwingStatus.WATCH
            if set(missing) <= soft or (rvol is not None and rvol >= Decimal("1.0"))
            else SwingStatus.NO_TRADE
        )
        if rvol is None or rvol < Decimal("1.0"):
            status = SwingStatus.NO_TRADE
        status, congress_note, meta = apply_congress_soft_overlay(
            status,
            signals=ctx.catalyst_signals,
            enabled=spec.congress_catalyst_enabled,
            soft_only=spec.congress_soft_only,
            meta=meta,
        )
        catalyst = ";".join(t for t in [";".join(missing), congress_note] if t) or "none"
        return SwingDecision(
            agent_id=agent,
            symbol=symbol,
            status=status,
            cap_tier=tier,
            catalyst=catalyst,
            volume_analysis=volume_analysis,
            rvol=rvol,
            reason=";".join(missing),
            hold_plan=spec.hold_plan_for_tier(tier),
            meta=meta,
        )

    entry = bar.close
    shared = SWING_SHARED
    if tier == CapTier.MICRO:
        target_pct = shared.final_target_pct_penny / Decimal("100")
        stop_pct = shared.stop_loss_pct_penny / Decimal("100")
    else:
        target_pct = shared.final_target_pct / Decimal("100")
        stop_pct = shared.stop_loss_pct / Decimal("100")
    scale_pct = shared.scale_out_gain_pct / Decimal("100")
    target = entry * (Decimal("1") + target_pct)
    stop = entry * (Decimal("1") - stop_pct)
    scale = entry * (Decimal("1") + scale_pct)

    status = SwingStatus.ENTER
    entry_window = "rth"
    if ctx.in_power_hour:
        entry_window = "power_hour"
    elif ctx.ten_am_divergence and spec.ten_am_sniper_enabled:
        entry_window = "ten_am_sniper"

    status, congress_note, meta = apply_congress_soft_overlay(
        status,
        signals=ctx.catalyst_signals,
        enabled=spec.congress_catalyst_enabled,
        soft_only=spec.congress_soft_only,
        meta=meta,
    )

    trade_map = None
    if status == SwingStatus.ENTER:
        trade_map = SwingTradeMap(
            entry_trigger=entry,
            scale_out_point=scale,
            final_take_profit=target,
            stop_loss=stop,
        )

    catalyst = congress_note or "setup_ok"
    if meta.get("congress_tags"):
        catalyst = f"{catalyst};" + ",".join(meta["congress_tags"])

    return SwingDecision(
        agent_id=agent,
        symbol=symbol,
        status=status,
        cap_tier=tier,
        catalyst=catalyst,
        volume_analysis=volume_analysis,
        rvol=rvol,
        entry_window=entry_window,
        trade_map=trade_map,
        hold_plan=spec.hold_plan_for_tier(tier),
        side=Side.LONG,
        reason="all_gates_passed" if status == SwingStatus.ENTER else congress_note,
        meta=meta,
    )

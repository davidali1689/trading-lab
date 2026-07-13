"""Large-cap sniper evaluator — deterministic gates for the vertical slice."""

from __future__ import annotations

from decimal import Decimal

from trading_lab.agents.sniper.decision import SniperDecision, TradeMap
from trading_lab.agents.sniper.large_cap import LARGE_CAP_SNIPER, LargeCapSniperSpec
from trading_lab.agents.sniper.shared_execution import SNIPER_SHARED, SniperStatus
from trading_lab.agents.sniper.shared_execution import scale_out_price
from trading_lab.market_data.types import SessionContext
from trading_lab.schemas.trades import RunMode, Side


def _rvol(bars: list, lookback: int = 20) -> Decimal | None:
    if len(bars) < lookback + 1:
        return None
    last = bars[-1].volume
    avg = sum((b.volume for b in bars[-(lookback + 1) : -1]), Decimal("0")) / Decimal(
        lookback
    )
    if avg == 0:
        return None
    return last / avg


def evaluate_large_cap_sniper(
    ctx: SessionContext,
    *,
    mode: RunMode = RunMode.BACKTEST,
    spec: LargeCapSniperSpec = LARGE_CAP_SNIPER,
) -> SniperDecision:
    """Return ENTER / WATCH / NO_TRADE. Never forces a trade."""
    agent = spec.agent_id
    symbol = ctx.symbol
    bar = ctx.bar

    require_catalyst = spec.require_catalyst
    if mode == RunMode.BACKTEST and not spec.require_catalyst_in_backtest:
        require_catalyst = False

    rvol = ctx.rvol if ctx.rvol is not None else _rvol(ctx.bars)
    above_vwap = ctx.above_vwap
    if above_vwap is None and bar.vwap is not None:
        above_vwap = bar.close > bar.vwap

    spy_ok = True if ctx.spy_aligned is None else ctx.spy_aligned
    qqq_ok = True if ctx.qqq_aligned is None else ctx.qqq_aligned
    market_aligned = spy_ok or qqq_ok

    cap_ok = True
    if ctx.market_cap_usd is not None:
        cap_ok = ctx.market_cap_usd >= spec.min_market_cap_usd

    missing: list[str] = []
    if rvol is None or rvol < spec.min_rvol:
        missing.append(f"rvol<{spec.min_rvol}")
    if spec.require_price_above_vwap and not above_vwap:
        missing.append("below_vwap")
    if spec.require_aligned_with_spy_qqq and not market_aligned:
        missing.append("spy_qqq_not_aligned")
    if not cap_ok:
        missing.append("market_cap")
    if require_catalyst and not ctx.has_catalyst:
        missing.append("catalyst")

    # HVN/LVN deferred — not evaluated in v0
    volume_analysis = f"rvol={rvol}; hvn_lvn=deferred"

    if missing:
        # Partial setup → WATCH; empty core → NO_TRADE
        soft = {"catalyst", "below_vwap"}
        status = (
            SniperStatus.WATCH
            if set(missing) <= soft or (rvol is not None and rvol >= Decimal("1.0"))
            else SniperStatus.NO_TRADE
        )
        if "rvol" in "".join(missing) and (rvol is None or rvol < Decimal("1.0")):
            status = SniperStatus.NO_TRADE
        return SniperDecision(
            agent_id=agent,
            symbol=symbol,
            status=status,
            catalyst="present" if ctx.has_catalyst else "none/relaxed",
            volume_analysis=volume_analysis,
            rvol=rvol,
            reason=";".join(missing),
            meta={"missing_gates": missing, "found_by_agent": agent},
        )

    entry = bar.close
    target_pct = spec.default_profit_target_pct / Decimal("100")
    stop_pct = spec.default_stop_loss_pct / Decimal("100")
    target = entry * (Decimal("1") + target_pct)
    stop = entry * (Decimal("1") - stop_pct)
    scale = scale_out_price(entry, target)

    return SniperDecision(
        agent_id=agent,
        symbol=symbol,
        status=SniperStatus.ENTER,
        catalyst="present" if ctx.has_catalyst else "relaxed_in_backtest",
        volume_analysis=volume_analysis,
        rvol=rvol,
        trade_map=TradeMap(
            entry_trigger=entry,
            scale_out_point=scale,
            final_take_profit=target,
            stop_loss=stop,
        ),
        hold_plan=SNIPER_SHARED.default_hold_plan,
        side=Side.LONG,
        reason="all_gates_passed",
        meta={"found_by_agent": agent, "hvn_lvn": "deferred"},
    )

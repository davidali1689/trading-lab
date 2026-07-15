"""Speculative sniper evaluator — micro-cap gates for paper/live ticks."""

from __future__ import annotations

from decimal import Decimal

from trading_lab.agents.sniper.decision import SniperDecision, TradeMap
from trading_lab.agents.sniper.shared_execution import SNIPER_SHARED, SniperStatus, scale_out_price
from trading_lab.agents.sniper.speculative import SPECULATIVE_SNIPER, SpeculativeSniperSpec
from trading_lab.market_data.types import SessionContext
from trading_lab.schemas.trades import RunMode, Side


def _rvol(bars: list, lookback: int = 20) -> Decimal | None:
    if len(bars) < lookback + 1:
        return None
    last = bars[-1].volume
    avg = sum((b.volume for b in bars[-(lookback + 1) : -1]), Decimal("0")) / Decimal(lookback)
    if avg == 0:
        return None
    return last / avg


def evaluate_speculative_sniper(
    ctx: SessionContext,
    *,
    mode: RunMode = RunMode.BACKTEST,
    spec: SpeculativeSniperSpec = SPECULATIVE_SNIPER,
) -> SniperDecision:
    """Return ENTER / WATCH / NO_TRADE. Never forces a trade."""
    agent = spec.agent_id
    symbol = ctx.symbol
    bar = ctx.bar

    require_catalyst = spec.require_catalyst
    if mode == RunMode.BACKTEST and not spec.require_catalyst_in_backtest:
        require_catalyst = False
    if mode == RunMode.PAPER and not spec.require_catalyst_in_paper:
        require_catalyst = False

    min_rvol = spec.min_rvol_paper if mode == RunMode.PAPER else spec.min_rvol

    rvol = ctx.rvol if ctx.rvol is not None else _rvol(ctx.bars)

    cap_ok = True
    if ctx.market_cap_usd is not None:
        cap_ok = ctx.market_cap_usd < spec.max_market_cap_usd

    # Float / RSI: skip gate when unknown (common on paper screener path).
    float_ok = True
    if ctx.float_shares is not None:
        float_ok = ctx.float_shares <= spec.max_float_shares

    rsi_ok = True
    if ctx.rsi is not None:
        rsi_ok = ctx.rsi < spec.max_rsi

    missing: list[str] = []
    if rvol is None or rvol < min_rvol:
        missing.append(f"rvol<{min_rvol}")
    if not cap_ok:
        missing.append("market_cap")
    if not float_ok:
        missing.append("float")
    if not rsi_ok:
        missing.append("rsi")
    if require_catalyst and not ctx.has_catalyst:
        missing.append("catalyst")

    volume_analysis = f"rvol={rvol}; float={ctx.float_shares}; rsi={ctx.rsi}"

    if missing:
        soft = {"catalyst", "float", "rsi"}
        status = (
            SniperStatus.WATCH
            if set(missing) <= soft or (rvol is not None and rvol >= Decimal("2.0"))
            else SniperStatus.NO_TRADE
        )
        if rvol is None or rvol < Decimal("1.0"):
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
        catalyst="present" if ctx.has_catalyst else "relaxed_in_paper",
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
        meta={"found_by_agent": agent},
    )

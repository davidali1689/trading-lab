"""gainer_sniper evaluator — first-hour early-band gates. Never forces a trade."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from trading_lab.agents.sniper.decision import SniperDecision, TradeMap
from trading_lab.agents.sniper.gainer import GAINER_SNIPER, GainerSniperSpec
from trading_lab.agents.sniper.shared_execution import SNIPER_SHARED, SniperStatus, scale_out_price
from trading_lab.market_data.types import SessionContext
from trading_lab.schemas.trades import RunMode, Side
from trading_lab.selection.gainer_scan import in_gainer_window


def _rvol(bars: list, lookback: int | None = None) -> Decimal | None:
    lookback = lookback if lookback is not None else max(len(bars) - 1, 1)
    if len(bars) < lookback + 1:
        return None
    last = bars[-1].volume
    avg = sum((b.volume for b in bars[-(lookback + 1) : -1]), Decimal("0")) / Decimal(lookback)
    if avg == 0:
        return None
    return last / avg


def evaluate_gainer_sniper(
    ctx: SessionContext,
    *,
    mode: RunMode = RunMode.BACKTEST,
    spec: GainerSniperSpec = GAINER_SNIPER,
    now_et: datetime | None = None,
    day_gain_pct: Decimal | None = None,
    on_live_gainer_list: bool = False,
) -> SniperDecision:
    """Return ENTER / WATCH / NO_TRADE for a live first-hour gainer."""
    agent = spec.agent_id
    symbol = ctx.symbol
    bar = ctx.bar
    min_rvol = spec.min_rvol_paper if mode == RunMode.PAPER else spec.min_rvol
    rvol = ctx.rvol if ctx.rvol is not None else _rvol(ctx.bars, lookback=spec.min_bars - 1)

    missing: list[str] = []
    if not in_gainer_window(now_et):
        missing.append("outside_window")
    if not on_live_gainer_list:
        missing.append("not_on_gainer_list")
    if day_gain_pct is None or day_gain_pct < spec.min_day_gain_pct:
        missing.append("day_gain")
    elif day_gain_pct >= spec.max_day_gain_pct:
        missing.append("day_gain")
    if len(ctx.bars) < spec.min_bars:
        missing.append(f"bars<{spec.min_bars}")
    if bar.close < spec.min_price or bar.close > spec.max_price:
        missing.append("price")
    above_vwap = ctx.above_vwap
    if above_vwap is None and bar.vwap is not None:
        above_vwap = bar.close > bar.vwap
    if above_vwap is None and mode in {RunMode.PAPER, RunMode.BACKTEST}:
        above_vwap = True
    if spec.require_price_above_vwap and not above_vwap:
        missing.append("below_vwap")
    if rvol is None or rvol < min_rvol:
        missing.append(f"rvol<{min_rvol}")

    volume_analysis = f"rvol={rvol}; day_gain={day_gain_pct}"
    if missing:
        return SniperDecision(
            agent_id=agent,
            symbol=symbol,
            status=SniperStatus.NO_TRADE,
            catalyst="gainer_list",
            volume_analysis=volume_analysis,
            rvol=rvol,
            reason=";".join(missing),
            meta={"missing_gates": missing, "found_by_agent": agent},
        )

    entry = bar.close
    target = entry * (Decimal("1") + spec.profit_target_pct / Decimal("100"))
    stop = entry * (Decimal("1") - spec.stop_loss_pct / Decimal("100"))
    return SniperDecision(
        agent_id=agent,
        symbol=symbol,
        status=SniperStatus.ENTER,
        catalyst="gainer_list",
        volume_analysis=volume_analysis,
        rvol=rvol,
        trade_map=TradeMap(
            entry_trigger=entry,
            scale_out_point=scale_out_price(entry, target),
            final_take_profit=target,
            stop_loss=stop,
        ),
        hold_plan=SNIPER_SHARED.default_hold_plan,
        side=Side.LONG,
        reason="all_gates_passed",
        meta={"found_by_agent": agent},
    )

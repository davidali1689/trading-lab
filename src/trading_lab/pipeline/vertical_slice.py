"""Vertical slice: mock bars → large_cap_sniper → budget risk → fill → journal.

Budget always comes from platform equity (or an explicit equity= for offline
tests). Never a hardcoded dollar budget.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

from trading_lab.agents.sniper.shared_execution import SniperStatus
from trading_lab.config.secrets import has_alpaca_keys
from trading_lab.eval.large_cap import evaluate_large_cap_sniper
from trading_lab.execution.budget import risk_config_from_equity, slice_notional
from trading_lab.execution.fill_model import DEFAULT_FILL_MODEL, FillModel
from trading_lab.execution.risk_gate import RiskGate
from trading_lab.journal.sqlite import SqliteJournal
from trading_lab.market_data.mock import MockMarketData
from trading_lab.market_data.types import BarRequest, SessionContext
from trading_lab.pipeline.paper_submit import qty_for_price
from trading_lab.schemas.hold import HoldPlan
from trading_lab.schemas.trades import (
    ExitReason,
    RunMode,
    Side,
    SkipEvent,
    SkipReason,
    TradeRecord,
)


def _platform_equity() -> Decimal:
    """Current Alpaca account equity — required for budget slices."""
    from trading_lab.broker.alpaca import AlpacaPaperBroker

    acct = AlpacaPaperBroker().get_account()
    if acct.equity is None or acct.equity <= 0:
        raise RuntimeError("platform equity unavailable or non-positive")
    return acct.equity


def run_vertical_slice(
    *,
    symbol: str = "AAPL",
    journal_path: str = "data/journal.sqlite",
    qty: Decimal | None = None,
    equity: Decimal | None = None,
    market_cap_usd: Decimal = Decimal("3000000000000"),
    mode: RunMode = RunMode.BACKTEST,
    fill_model: FillModel = DEFAULT_FILL_MODEL,
) -> dict:
    """End-to-end local run with honest next-bar fills. Returns summary counts.

    Position size = one budget slice of current equity (equity/5). Max 3 opens.
    Pass equity= only for offline tests; otherwise read Alpaca.
    """
    run_id = uuid4()
    end = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    start = end - timedelta(hours=6)
    md = MockMarketData()
    bars = md.get_bars(
        BarRequest(symbol=symbol, timeframe="1Min", start=start, end=end, feed="iex")
    )
    journal = SqliteJournal(journal_path)
    if equity is not None:
        book_equity = equity
    elif has_alpaca_keys():
        book_equity = _platform_equity()
    else:
        raise RuntimeError(
            "budget needs platform equity — configure Alpaca keys or pass equity= for tests"
        )
    notional = slice_notional(book_equity)
    risk = RiskGate(config=risk_config_from_equity(book_equity))
    trades: list[TradeRecord] = []
    skips: list[SkipEvent] = []

    i = 20
    while i < len(bars) - 2:
        window = bars[: i + 1]
        bar = bars[i]
        ctx = SessionContext(
            symbol=symbol,
            bar=bar,
            bars=window,
            market_cap_usd=market_cap_usd,
            has_catalyst=False,  # relaxed in backtest
            spy_aligned=True,
            qqq_aligned=True,
        )
        decision = evaluate_large_cap_sniper(ctx, mode=mode)
        agent = decision.agent_id

        if decision.status != SniperStatus.ENTER or decision.trade_map is None:
            reason = (
                SkipReason.SETUP_MISSING
                if decision.status == SniperStatus.NO_TRADE
                else SkipReason.OUTSIDE_WINDOW
            )
            skip = SkipEvent(
                event_id=uuid4(),
                run_id=run_id,
                found_by_agent=agent,
                symbol=symbol,
                ts=bar.ts,
                mode=mode,
                skip_reason=reason,
                detail=decision.reason or decision.status.value,
                bar_ts=bar.ts,
                meta={"sniper_status": decision.status.value},
            )
            skips.append(skip)
            journal.write_skip(skip)
            i += 1
            continue

        trade_qty = (
            qty if qty is not None else qty_for_price(decision.trade_map.entry_trigger, notional)
        )
        if trade_qty is None:
            skip = SkipEvent(
                event_id=uuid4(),
                run_id=run_id,
                found_by_agent=agent,
                symbol=symbol,
                ts=bar.ts,
                mode=mode,
                skip_reason=SkipReason.RISK_BLOCKED,
                detail="slice_cannot_buy_1_share",
                bar_ts=bar.ts,
            )
            skips.append(skip)
            journal.write_skip(skip)
            i += 1
            continue
        intent = decision.to_trade_intent(trade_qty)
        assert intent is not None
        gate = risk.check(intent, bar.ts)
        if not gate.allowed:
            skip = SkipEvent(
                event_id=uuid4(),
                run_id=run_id,
                found_by_agent=agent,
                symbol=symbol,
                ts=bar.ts,
                mode=mode,
                skip_reason=gate.skip_reason or SkipReason.RISK_BLOCKED,
                detail=gate.detail,
                bar_ts=bar.ts,
            )
            skips.append(skip)
            journal.write_skip(skip)
            i += 1
            continue

        next_bar = bars[i + 1]
        entry_px = fill_model.fill_price(bar, next_bar, Side.LONG)
        risk.on_open()

        exit_px = entry_px
        exit_reason = ExitReason.TIME
        exit_ts = next_bar.ts
        bars_held = 1
        stop_hit = False
        j = i + 2
        while j < len(bars) and bars_held < 60:
            b = bars[j]
            if decision.trade_map.stop_loss is not None and b.low <= decision.trade_map.stop_loss:
                exit_px = fill_model.exit_fill(b, Side.LONG, is_stop=True)
                exit_reason = ExitReason.STOP
                exit_ts = b.ts
                stop_hit = True
                break
            if (
                decision.trade_map.final_take_profit is not None
                and b.high >= decision.trade_map.final_take_profit
            ):
                exit_px = fill_model.exit_fill(b, Side.LONG, is_stop=False)
                exit_reason = ExitReason.TARGET
                exit_ts = b.ts
                break
            exit_px = b.close
            exit_ts = b.ts
            bars_held += 1
            j += 1

        hold: HoldPlan = intent.hold_plan
        trade = TradeRecord(
            trade_id=uuid4(),
            run_id=run_id,
            found_by_agent=agent,
            symbol=symbol,
            side=Side.LONG,
            mode=mode,
            setup_tags=intent.setup_tags,
            entry_ts=next_bar.ts,
            entry_px=entry_px,
            qty=trade_qty,
            stop_px=decision.trade_map.stop_loss,
            target_px=decision.trade_map.final_take_profit,
            hold_plan=hold,
            exit_ts=exit_ts,
            exit_px=exit_px,
            exit_reason=exit_reason,
            bars_held=bars_held,
            fill_model=fill_model.style.value,
            slippage_bps=fill_model.slippage_bps,
            meta={
                "found_by_agent": agent,
                "equity": str(book_equity),
                "notional_usd": str(notional),
                "budget_slice": "1/5",
            },
        )
        risk.on_close(trade.pnl_usd, stop_hit=stop_hit, now=exit_ts)
        trades.append(trade)
        journal.write_trade(trade)
        i = max(j, i + bars_held + 1)

    return {
        "run_id": str(run_id),
        "trades": len(trades),
        "skips": len(skips),
        "journal_path": journal_path,
        "equity": str(book_equity),
        "slice_notional": str(notional),
        "found_by_agents": sorted({t.found_by_agent for t in trades}),
    }

"""2026-08-11 audit remediations.

1. Per-trade dollar risk cap (avg stop −$495 vs avg winner +$121).
2. Tick-time product check uses the asset name cached on the watchlist.
3. Watchlist bar-coverage gate (ALGS: 0-bar symbol occupying a slot all day).
4. Dedicated swing universe scan on daily-bar gates.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

import pytest

from trading_lab.broker.alpaca import AlpacaPaperBroker
from trading_lab.broker.types import BrokerAccount, BrokerOrderResult
from trading_lab.execution.budget import cap_qty_by_risk
from trading_lab.market_data.alpaca_screener import AssetMeta, ScreenerRow
from trading_lab.market_data.types import Bar
from trading_lab.pipeline import paper_agents
from trading_lab.pipeline.paper_agents import run_sniper_paper_tick
from trading_lab.selection.swing_watchlist import build_swing_watchlist
from trading_lab.selection.watchlist import (
    WatchlistCandidate,
    WatchlistDocument,
    build_daily_watchlist,
)

ET = ZoneInfo("America/New_York")
FIXED_NOW_ET = datetime(2026, 8, 4, 10, 30, tzinfo=ET)
BAR_DAY = datetime(2026, 8, 4, 13, 30, tzinfo=UTC)


# --- shared fakes -----------------------------------------------------------


class MinuteBars:
    """30 rising 1Min bars with a final RVOL spike (sniper ENTER shape)."""

    def get_bars(self, request) -> list[Bar]:
        bars: list[Bar] = []
        px = Decimal("100")
        if "Min" in request.timeframe:
            for i in range(30):
                vol = Decimal("1500000") if i == 29 else Decimal("100000")
                bars.append(
                    Bar(
                        symbol=request.symbol,
                        ts=BAR_DAY + timedelta(minutes=i),
                        open=px,
                        high=px + Decimal("0.2"),
                        low=px - Decimal("0.05"),
                        close=px + Decimal("0.1"),
                        volume=vol,
                        vwap=px + Decimal("0.05"),
                        timeframe=request.timeframe,
                    )
                )
                px += Decimal("0.1")
        else:
            px = Decimal("90")
            for i in range(30):
                bars.append(
                    Bar(
                        symbol=request.symbol,
                        ts=BAR_DAY - timedelta(days=30 - i),
                        open=px,
                        high=px + Decimal("0.5"),
                        low=px - Decimal("0.5"),
                        close=px + Decimal("0.6"),
                        volume=Decimal("5000000"),
                        vwap=px,
                        timeframe=request.timeframe,
                    )
                )
                px += Decimal("0.6")
        return bars


class SubmitBroker(AlpacaPaperBroker):
    """No-HTTP paper broker double that records submitted intents."""

    def __init__(self) -> None:
        self.submitted: list = []

    def get_account(self) -> BrokerAccount:
        return BrokerAccount(
            equity=Decimal("100000"),
            cash=Decimal("80000"),
            buying_power=Decimal("80000"),
            paper=True,
            settled_cash=Decimal("80000"),
        )

    def get_open_positions(self) -> list:
        return []

    def has_open_position(self, symbol: str) -> bool:
        return False

    def list_open_orders(self, symbol: str | None = None) -> list[dict]:
        return []

    def submit_bracket_order(self, intent) -> BrokerOrderResult:
        self.submitted.append(intent)
        return BrokerOrderResult(
            order_id="fake-order-1",
            symbol=intent.symbol,
            status="filled",
            qty=intent.qty,
            raw={},
            filled_avg_price=intent.entry_px,
        )

    def wait_for_fill(self, order_id: str, **kwargs) -> BrokerOrderResult:
        intent = self.submitted[-1]
        return BrokerOrderResult(
            order_id=order_id,
            symbol=intent.symbol,
            status="filled",
            qty=intent.qty,
            raw={},
            filled_avg_price=intent.entry_px,
        )

    def get_order(self, order_id: str) -> dict:
        return {
            "id": order_id,
            "status": "filled",
            "legs": [
                {"side": "sell", "type": "limit"},
                {"side": "sell", "type": "stop"},
            ],
        }


def _tick(symbol: str, broker: SubmitBroker, journal_path: str, agent_id: str) -> dict:
    with (
        patch.object(paper_agents, "resolve_market_data", return_value=MinuteBars()),
        patch.object(paper_agents, "_paper_has_catalyst", return_value=True),
        patch.object(paper_agents, "now_et", return_value=FIXED_NOW_ET),
    ):
        return run_sniper_paper_tick(
            symbol=symbol,
            journal_path=journal_path,
            agent_id=agent_id,
            market_cap_usd=None,
            broker=broker,
        )


# --- 1. per-trade risk cap --------------------------------------------------


def test_cap_qty_by_risk_shrinks_position() -> None:
    # $100k equity → $250 cap; $2 risk/share → 125 shares max.
    qty = cap_qty_by_risk(
        entry_px=Decimal("50"),
        stop_px=Decimal("48"),
        qty=Decimal("400"),
        equity=Decimal("100000"),
    )
    assert qty == Decimal("125")


def test_cap_qty_by_risk_leaves_small_positions() -> None:
    qty = cap_qty_by_risk(
        entry_px=Decimal("50"),
        stop_px=Decimal("48"),
        qty=Decimal("100"),
        equity=Decimal("100000"),
    )
    assert qty == Decimal("100")


def test_cap_qty_by_risk_no_stop_passthrough() -> None:
    qty = cap_qty_by_risk(
        entry_px=Decimal("50"),
        stop_px=None,
        qty=Decimal("400"),
        equity=Decimal("100000"),
    )
    assert qty == Decimal("400")


def test_cap_qty_by_risk_zero_when_one_share_busts_cap() -> None:
    qty = cap_qty_by_risk(
        entry_px=Decimal("20000"),
        stop_px=Decimal("19000"),
        qty=Decimal("1"),
        equity=Decimal("100000"),
    )
    assert qty == Decimal("0")


def test_cap_qty_by_risk_disabled_via_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAX_TRADE_RISK_PCT", "0")
    qty = cap_qty_by_risk(
        entry_px=Decimal("50"),
        stop_px=Decimal("48"),
        qty=Decimal("400"),
        equity=Decimal("100000"),
    )
    assert qty == Decimal("400")


def test_sniper_tick_submits_within_risk_cap(tmp_path: Path) -> None:
    """End to end: submitted bracket risks ≤ equity × MAX_TRADE_RISK_PCT."""
    broker = SubmitBroker()
    out = _tick("UPC", broker, str(tmp_path / "j.sqlite"), "speculative_sniper")
    assert out["status"] == "ORDER_SUBMITTED", out
    intent = broker.submitted[-1]
    assert intent.stop_px is not None
    risk_usd = (intent.entry_px - intent.stop_px) * intent.qty
    assert risk_usd <= Decimal("100000") * Decimal("0.25") / 100
    assert intent.qty >= 1


# --- 2. tick-time product check via cached asset name ------------------------


def test_tick_blocks_etf_by_watchlist_name(tmp_path: Path) -> None:
    """SPCX-class gap: product name cached on the watchlist blocks the tick."""
    doc = WatchlistDocument(
        symbols=["FKEX"],
        candidates=[
            WatchlistCandidate(
                symbol="FKEX",
                price="137.31",
                name="Fake Exchange Traded Fund ETF",
            )
        ],
        source="s3",
        built_at="2026-08-11T12:00:00+00:00",
        size=1,
    )
    broker = SubmitBroker()
    with patch.object(paper_agents, "get_watchlist", return_value=doc):
        out = _tick("FKEX", broker, str(tmp_path / "j.sqlite"), "large_cap_sniper")
    assert out["status"] == "SKIP"
    assert out["detail"] == "disallowed_product"
    assert broker.submitted == []


def test_tick_allows_common_stock_name(tmp_path: Path) -> None:
    doc = WatchlistDocument(
        symbols=["UPC"],
        candidates=[
            WatchlistCandidate(symbol="UPC", price="103", name="UPC Common Stock")
        ],
        source="s3",
        built_at="2026-08-11T12:00:00+00:00",
        size=1,
    )
    broker = SubmitBroker()
    with patch.object(paper_agents, "get_watchlist", return_value=doc):
        out = _tick("UPC", broker, str(tmp_path / "j.sqlite"), "speculative_sniper")
    assert out["status"] == "ORDER_SUBMITTED", out


# --- 3. watchlist bar-coverage gate ------------------------------------------


def _row(symbol: str, **kw) -> ScreenerRow:
    defaults = dict(
        source="gainer",
        price=Decimal("50"),
        volume=Decimal("5000000"),
        percent_change=Decimal("5"),
    )
    defaults.update(kw)
    return ScreenerRow(symbol=symbol, **defaults)


def _asset(symbol: str, name: str = "") -> AssetMeta:
    return AssetMeta(
        symbol=symbol,
        tradable=True,
        status="active",
        asset_class="us_equity",
        exchange="NASDAQ",
        name=name or f"{symbol} Common Stock",
    )


class SparseBars:
    """Thin tape: a handful of bars for THIN, plenty for everything else."""

    def get_bars(self, request) -> list[Bar]:
        n = 3 if request.symbol == "THIN" else 200
        return [
            Bar(
                symbol=request.symbol,
                ts=datetime.now(UTC) - timedelta(minutes=i),
                open=Decimal("10"),
                high=Decimal("10.1"),
                low=Decimal("9.9"),
                close=Decimal("10"),
                volume=Decimal("1000"),
                timeframe=request.timeframe,
            )
            for i in range(n)
        ]


def test_watchlist_drops_zero_coverage_symbols() -> None:
    screener = MagicMock()
    screener.most_actives.return_value = []
    screener.movers.return_value = [_row("THIN"), _row("DENS")]
    screener.asset.side_effect = lambda sym: _asset(sym)

    doc = build_daily_watchlist(
        screener=screener, size=12, verify_assets=True, market_data=SparseBars()
    )
    assert "THIN" not in doc.symbols
    assert "DENS" in doc.symbols


def test_watchlist_coverage_gate_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MIN_WATCHLIST_PREV_BARS", "0")
    screener = MagicMock()
    screener.most_actives.return_value = []
    screener.movers.return_value = [_row("THIN")]
    screener.asset.side_effect = lambda sym: _asset(sym)

    doc = build_daily_watchlist(
        screener=screener, size=12, verify_assets=True, market_data=SparseBars()
    )
    assert "THIN" in doc.symbols


def test_watchlist_candidate_carries_asset_name() -> None:
    screener = MagicMock()
    screener.most_actives.return_value = []
    screener.movers.return_value = [_row("DENS")]
    screener.asset.side_effect = lambda sym: _asset(sym, name="Dens Industries Inc.")

    doc = build_daily_watchlist(
        screener=screener, size=12, verify_assets=True, market_data=SparseBars()
    )
    cand = next(c for c in doc.candidates if c.symbol == "DENS")
    assert cand.name == "Dens Industries Inc."


# --- 4. swing universe scan ---------------------------------------------------


class DailyBars:
    """MOMO: uptrend above 8-EMA with a volume spike. FLAT: no rvol, drifting down."""

    def get_bars(self, request) -> list[Bar]:
        bars: list[Bar] = []
        if request.symbol == "MOMO":
            px = Decimal("50")
            for i in range(30):
                vol = Decimal("9000000") if i == 29 else Decimal("3000000")
                bars.append(
                    Bar(
                        symbol=request.symbol,
                        ts=datetime.now(UTC) - timedelta(days=30 - i),
                        open=px,
                        high=px + Decimal("1"),
                        low=px - Decimal("0.5"),
                        close=px + Decimal("0.8"),
                        volume=vol,
                        timeframe=request.timeframe,
                    )
                )
                px += Decimal("0.8")
        else:
            px = Decimal("50")
            for i in range(30):
                bars.append(
                    Bar(
                        symbol=request.symbol,
                        ts=datetime.now(UTC) - timedelta(days=30 - i),
                        open=px,
                        high=px + Decimal("0.2"),
                        low=px - Decimal("0.4"),
                        close=px - Decimal("0.3"),
                        volume=Decimal("3000000"),
                        timeframe=request.timeframe,
                    )
                )
                px -= Decimal("0.3")
        return bars


def test_swing_watchlist_selects_daily_momentum() -> None:
    screener = MagicMock()
    screener.most_actives.return_value = []
    screener.movers.return_value = [_row("MOMO"), _row("FLAT")]
    screener.asset.side_effect = lambda sym: _asset(sym)

    doc = build_swing_watchlist(screener=screener, market_data=DailyBars(), size=12)
    assert "MOMO" in doc.symbols
    assert "FLAT" not in doc.symbols
    cand = next(c for c in doc.candidates if c.symbol == "MOMO")
    assert cand.reason == "swing_scan_pass"


def test_swing_watchlist_rejects_products_and_penny() -> None:
    screener = MagicMock()
    screener.most_actives.return_value = []
    screener.movers.return_value = [
        _row("MOMO"),
        _row("PLTU"),  # static leveraged list
        _row("CHEAP", price=Decimal("1.50")),
    ]
    screener.asset.side_effect = lambda sym: _asset(sym)

    doc = build_swing_watchlist(screener=screener, market_data=DailyBars(), size=12)
    assert doc.symbols == ["MOMO"]


def test_swing_watchlist_empty_on_scan_failure() -> None:
    screener = MagicMock()
    screener.most_actives.side_effect = RuntimeError("alpaca down")
    doc = build_swing_watchlist(screener=screener, market_data=DailyBars())
    assert doc.symbols == []
    assert doc.source == "empty"


def test_watchlist_meta_helper_reads_cached_name() -> None:
    doc = WatchlistDocument(
        symbols=["FKEX"],
        candidates=[WatchlistCandidate(symbol="FKEX", name="Fake 2X Leveraged ETF")],
        source="s3",
        built_at="2026-08-11T12:00:00+00:00",
        size=1,
    )
    with patch.object(paper_agents, "get_watchlist", return_value=doc):
        meta = paper_agents._watchlist_asset_meta("FKEX")
        assert isinstance(meta, SimpleNamespace)
        assert meta.name == "Fake 2X Leveraged ETF"
        assert paper_agents._watchlist_asset_meta("ZZZZ") is None

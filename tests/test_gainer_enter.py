"""gainer_sniper ENTER only in the first-hour early band — not EOD chase."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from trading_lab.agents.sniper.shared_execution import SniperStatus
from trading_lab.eval.gainer import evaluate_gainer_sniper
from trading_lab.market_data.types import Bar, SessionContext
from trading_lab.schemas.trades import RunMode

ET = ZoneInfo("America/New_York")


def _bars(
    n: int = 10, *, close: Decimal = Decimal("10"), vwap: Decimal = Decimal("9.8")
) -> list[Bar]:
    out: list[Bar] = []
    for i in range(n):
        vol = Decimal("100") if i < n - 1 else Decimal("250")
        out.append(
            Bar(
                symbol="FGI",
                ts=datetime(2026, 8, 13, 13, 40, i, tzinfo=ET),
                open=close,
                high=close + Decimal("0.2"),
                low=close - Decimal("0.1"),
                close=close,
                volume=vol,
                vwap=vwap,
            )
        )
    return out


def _ctx(*, bars: list[Bar] | None = None, **kwargs: object) -> SessionContext:
    bars = bars or _bars()
    payload = {
        "symbol": "FGI",
        "bar": bars[-1],
        "bars": bars,
        "rvol": Decimal("2.0"),
        "above_vwap": True,
        "has_catalyst": False,
        "market_cap_usd": Decimal("500000000"),
    }
    payload.update(kwargs)
    return SessionContext(**payload)  # type: ignore[arg-type]


def _open_et(hour: int = 9, minute: int = 45) -> datetime:
    return datetime(2026, 8, 13, hour, minute, tzinfo=ET)


def test_paper_gainer_enters_early_band_without_catalyst() -> None:
    d = evaluate_gainer_sniper(
        _ctx(),
        mode=RunMode.PAPER,
        now_et=_open_et(),
        day_gain_pct=Decimal("8"),
        on_live_gainer_list=True,
    )
    assert d.status == SniperStatus.ENTER
    assert d.agent_id == "gainer_sniper"
    assert d.trade_map is not None
    entry = d.trade_map.entry_trigger
    assert d.trade_map.final_take_profit == entry * Decimal("1.06")
    assert d.trade_map.stop_loss == entry * Decimal("0.975")


def test_gainer_rejects_outside_window() -> None:
    d = evaluate_gainer_sniper(
        _ctx(),
        mode=RunMode.PAPER,
        now_et=_open_et(11, 0),
        day_gain_pct=Decimal("8"),
        on_live_gainer_list=True,
    )
    assert d.status != SniperStatus.ENTER
    assert "window" in (d.reason or "")


def test_gainer_rejects_not_on_live_list() -> None:
    d = evaluate_gainer_sniper(
        _ctx(),
        mode=RunMode.PAPER,
        now_et=_open_et(),
        day_gain_pct=Decimal("8"),
        on_live_gainer_list=False,
    )
    assert d.status != SniperStatus.ENTER
    assert "gainer_list" in (d.reason or "")


def test_gainer_rejects_already_extended() -> None:
    d = evaluate_gainer_sniper(
        _ctx(),
        mode=RunMode.PAPER,
        now_et=_open_et(),
        day_gain_pct=Decimal("15"),
        on_live_gainer_list=True,
    )
    assert d.status != SniperStatus.ENTER
    assert "day_gain" in (d.reason or "")


def test_gainer_rejects_too_little_move() -> None:
    d = evaluate_gainer_sniper(
        _ctx(),
        mode=RunMode.PAPER,
        now_et=_open_et(),
        day_gain_pct=Decimal("1.5"),
        on_live_gainer_list=True,
    )
    assert d.status != SniperStatus.ENTER
    assert "day_gain" in (d.reason or "")


def test_gainer_rejects_thin_bars() -> None:
    d = evaluate_gainer_sniper(
        _ctx(bars=_bars(9)),
        mode=RunMode.PAPER,
        now_et=_open_et(),
        day_gain_pct=Decimal("8"),
        on_live_gainer_list=True,
    )
    assert d.status != SniperStatus.ENTER
    assert "bars" in (d.reason or "")


def test_gainer_rejects_below_vwap() -> None:
    bars = _bars(vwap=Decimal("10.5"))
    d = evaluate_gainer_sniper(
        _ctx(bars=bars, above_vwap=False),
        mode=RunMode.PAPER,
        now_et=_open_et(),
        day_gain_pct=Decimal("8"),
        on_live_gainer_list=True,
    )
    assert d.status != SniperStatus.ENTER
    assert "vwap" in (d.reason or "")

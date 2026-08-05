"""Universe gates from 2026-08-05 session lessons (extension / ETF / large cluster)."""

from __future__ import annotations

from decimal import Decimal

from trading_lab.market_data.alpaca_screener import AssetMeta
from trading_lab.selection.universe_gates import (
    day_gain_too_extended,
    is_disallowed_product,
    max_day_gain_pct,
    max_open_large_cap,
)


def test_day_gain_too_extended_defaults() -> None:
    assert max_day_gain_pct() == Decimal("40")
    assert day_gain_too_extended(Decimal("434.25")) is True
    assert day_gain_too_extended(Decimal("40")) is True
    assert day_gain_too_extended(Decimal("39.9")) is False
    assert day_gain_too_extended(None) is False


def test_speculative_day_gain_ceiling_tighter_than_universe() -> None:
    from trading_lab.selection.universe_gates import (
        max_speculative_day_gain_pct,
        speculative_day_gain_too_extended,
    )

    assert max_speculative_day_gain_pct() == Decimal("25")
    # 25–39% would still pass the book-wide 40% gate but not speculative.
    assert day_gain_too_extended(Decimal("30")) is False
    assert speculative_day_gain_too_extended(Decimal("30")) is True
    assert speculative_day_gain_too_extended(Decimal("25")) is True
    assert speculative_day_gain_too_extended(Decimal("24.9")) is False
    assert speculative_day_gain_too_extended(None) is False


def test_leveraged_etf_symbol_denylist() -> None:
    assert is_disallowed_product("PLTU") is True
    assert is_disallowed_product("PLTG") is True
    assert is_disallowed_product("TQQQ") is True
    assert is_disallowed_product("NVDA") is False
    assert is_disallowed_product("AMIX") is False


def test_leveraged_or_etf_detected_from_asset_name() -> None:
    lev = AssetMeta(
        symbol="FAKE",
        tradable=True,
        status="active",
        asset_class="us_equity",
        exchange="NYSE",
        name="Direxion Daily Semiconductors Bull 3X Shares",
    )
    etf = AssetMeta(
        symbol="SPCX",
        tradable=True,
        status="active",
        asset_class="us_equity",
        exchange="NYSE",
        name="SP Funds S&P Kensho Future Technologies ETF",
    )
    stock = AssetMeta(
        symbol="INTC",
        tradable=True,
        status="active",
        asset_class="us_equity",
        exchange="NASDAQ",
        name="Intel Corporation Common Stock",
    )
    assert is_disallowed_product("FAKE", meta=lev) is True
    assert is_disallowed_product("SPCX", meta=etf) is True
    assert is_disallowed_product("INTC", meta=stock) is False


def test_max_open_large_cap_default() -> None:
    assert max_open_large_cap() == 2

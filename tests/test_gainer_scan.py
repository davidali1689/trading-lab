"""Live first-hour gainer scan — early band, no warrants/units, window union."""

from __future__ import annotations

import json
from datetime import datetime
from decimal import Decimal
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

from trading_lab.market_data.alpaca_screener import AssetMeta, ScreenerRow
from trading_lab.selection.gainer_scan import (
    in_gainer_window,
    is_unit_or_warrant,
    scan_live_gainers,
    union_tick_symbols,
)

ET = ZoneInfo("America/New_York")


def _row(
    symbol: str,
    *,
    percent_change: str = "8",
    price: str = "12",
) -> ScreenerRow:
    return ScreenerRow(
        symbol=symbol,
        source="gainer",
        price=Decimal(price),
        percent_change=Decimal(percent_change),
    )


def _asset(symbol: str, *, name: str = "") -> AssetMeta:
    return AssetMeta(
        symbol=symbol,
        tradable=True,
        status="active",
        asset_class="us_equity",
        exchange="NASDAQ",
        name=name or f"{symbol} Common Stock",
    )


def test_window_is_first_hour_et() -> None:
    assert in_gainer_window(datetime(2026, 8, 13, 9, 30, tzinfo=ET))
    assert in_gainer_window(datetime(2026, 8, 13, 10, 29, tzinfo=ET))
    assert not in_gainer_window(datetime(2026, 8, 13, 10, 30, tzinfo=ET))
    assert not in_gainer_window(datetime(2026, 8, 13, 8, 0, tzinfo=ET))


def test_five_letter_w_and_u_are_units_or_warrants() -> None:
    assert is_unit_or_warrant("BRUNW")
    assert is_unit_or_warrant("BCARU")
    assert not is_unit_or_warrant("NOW")
    assert not is_unit_or_warrant("FGI")


def test_scan_keeps_early_band_drops_chase_and_products() -> None:
    screener = MagicMock()
    screener.movers.return_value = [
        _row("FGI", percent_change="8"),
        _row("PLAG", percent_change="148"),
        _row("BRUNW", percent_change="10"),
        _row("PLTU", percent_change="12"),
        ScreenerRow(
            symbol="LOSE",
            source="loser",
            price=Decimal("10"),
            percent_change=Decimal("-5"),
        ),
    ]
    screener.asset.side_effect = lambda sym: _asset(
        sym,
        name="Direxion Daily PLTR Bull 2X Shares" if sym == "PLTU" else f"{sym} Common Stock",
    )

    rows = scan_live_gainers(screener, verify_assets=True)
    symbols = [r.symbol for r in rows]
    assert symbols == ["FGI"]


def test_union_adds_live_gainers_during_window_only() -> None:
    watch = ["NVDA", "INTC"]
    live = ["FGI", "COOL"]
    now = datetime(2026, 8, 13, 9, 45, tzinfo=ET)
    unioned = union_tick_symbols(watch, live, now_et=now)
    assert unioned[:2] == ["NVDA", "INTC"]
    assert "FGI" in unioned
    assert "COOL" in unioned

    after = union_tick_symbols(watch, live, now_et=datetime(2026, 8, 13, 11, 0, tzinfo=ET))
    assert after == ["NVDA", "INTC"]


def test_persist_first_hour_keeps_first_seen(monkeypatch) -> None:
    stored: dict[str, bytes] = {}

    class FakeS3:
        def put_object(self, *, Bucket: str, Key: str, Body: bytes, ContentType: str) -> None:
            stored[Key] = Body

        def get_object(self, *, Bucket: str, Key: str) -> dict:
            if Key not in stored:
                raise FileNotFoundError(Key)
            return {"Body": MagicMock(read=lambda: stored[Key])}

    monkeypatch.setenv("JOURNAL_S3_BUCKET", "test-bucket")
    from trading_lab.selection.gainer_scan import persist_first_hour_snapshot

    with patch("boto3.client", return_value=FakeS3()):
        persist_first_hour_snapshot([_row("FGI", percent_change="8")])
        persist_first_hour_snapshot(
            [_row("FGI", percent_change="12"), _row("COOL", percent_change="6")]
        )
    latest = next(k for k in stored if k.endswith("latest.json"))
    data = json.loads(stored[latest])
    by = {c["symbol"]: c["percent_change"] for c in data["candidates"]}
    assert by["FGI"] == "8"
    assert by["COOL"] == "6"

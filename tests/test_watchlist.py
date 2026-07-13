"""Dynamic watchlist — screener filters, empty fallback, phase hooks."""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from trading_lab.market_data.alpaca_screener import AssetMeta, ScreenerRow
from trading_lab.selection.watchlist import (
    build_daily_watchlist,
    load_watchlist,
    save_watchlist,
)


def _row(
    symbol: str,
    *,
    source: str = "gainer",
    price: str | None = "50",
    volume: str | None = "5000000",
    percent_change: str | None = "5",
) -> ScreenerRow:
    return ScreenerRow(
        symbol=symbol,
        source=source,
        price=Decimal(price) if price is not None else None,
        volume=Decimal(volume) if volume is not None else None,
        percent_change=Decimal(percent_change) if percent_change is not None else None,
    )


def _asset(symbol: str, *, tradable: bool = True, exchange: str = "NASDAQ") -> AssetMeta:
    return AssetMeta(
        symbol=symbol,
        tradable=tradable,
        status="active",
        asset_class="us_equity",
        exchange=exchange,
    )


def test_build_daily_watchlist_ranks_and_filters() -> None:
    screener = MagicMock()
    screener.most_actives.return_value = [
        _row("ZZZZ", source="most_actives", price=None, volume="9000000"),
        _row("PENNY", source="most_actives", price=None, volume="50"),
        _row("BAD.PR", source="most_actives", price=None, volume="9000000"),
    ]
    screener.movers.return_value = [
        _row("AAAA", source="gainer", price="120", percent_change="8"),
        _row("CHEAP", source="gainer", price="1.50", percent_change="40"),
        _row("OTCY", source="gainer", price="20", percent_change="10"),
    ]
    screener.asset.side_effect = lambda sym: (
        _asset(sym, exchange="OTC") if sym == "OTCY" else _asset(sym)
    )

    doc = build_daily_watchlist(screener=screener, size=12, verify_assets=True)

    assert "CHEAP" not in doc.symbols
    assert "BAD.PR" not in doc.symbols
    assert "PENNY" not in doc.symbols
    assert "OTCY" not in doc.symbols
    assert "AAAA" in doc.symbols
    assert "ZZZZ" in doc.symbols
    assert doc.source == "fresh_scan"
    assert "AAPL" not in doc.symbols
    assert "MSFT" not in doc.symbols
    assert "SPY" not in doc.symbols


def test_build_never_hardcodes_on_failure() -> None:
    screener = MagicMock()
    screener.most_actives.side_effect = RuntimeError("alpaca down")
    doc = build_daily_watchlist(screener=screener, size=12)
    assert doc.symbols == []
    assert doc.source == "empty"
    assert "scan_failed" in doc.detail
    assert doc.symbols != ["AAPL", "MSFT", "SPY"]


def test_build_respects_size_cap() -> None:
    screener = MagicMock()
    # Alphabetical tickers only (common-equity heuristic rejects digits)
    names = [f"{chr(65 + i)}{chr(66 + i)}{chr(67 + i)}" for i in range(20)]
    movers = [
        _row(name, source="gainer", price="30", percent_change=str(20 - i))
        for i, name in enumerate(names)
    ]
    screener.most_actives.return_value = []
    screener.movers.return_value = movers
    screener.asset.side_effect = lambda sym: _asset(sym)

    doc = build_daily_watchlist(screener=screener, size=5, verify_assets=True)
    assert len(doc.symbols) == 5
    assert doc.size == 5


def test_save_and_load_watchlist_roundtrip(monkeypatch: pytest.MonkeyPatch) -> None:
    stored: dict[str, bytes] = {}

    class FakeS3:
        def put_object(self, *, Bucket: str, Key: str, Body: bytes, ContentType: str) -> None:
            stored[Key] = Body

        def get_object(self, *, Bucket: str, Key: str) -> dict:
            if Key not in stored:
                raise FileNotFoundError(Key)
            return {"Body": MagicMock(read=lambda: stored[Key])}

    monkeypatch.setenv("JOURNAL_S3_BUCKET", "test-bucket")
    fake_boto = MagicMock()
    fake_boto.client.return_value = FakeS3()

    screener = MagicMock()
    screener.most_actives.return_value = []
    screener.movers.return_value = [_row("ABCD", price="40", percent_change="3")]
    screener.asset.return_value = _asset("ABCD")
    doc = build_daily_watchlist(screener=screener, size=3, verify_assets=True)

    with patch.dict("sys.modules", {"boto3": fake_boto}):
        # save/load import boto3 inside function — patch at import site via module
        with patch("boto3.client", return_value=FakeS3()):
            saved = save_watchlist(doc, bucket="test-bucket")
            assert saved["ok"] is True
            loaded = load_watchlist(bucket="test-bucket")

    assert loaded.symbols == ["ABCD"]
    assert loaded.source == "s3"


def test_tick_empty_watchlist_noop(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SECRET_ARN", "")
    monkeypatch.setenv("KILL_SWITCH", "0")
    from api import server

    client = TestClient(server.app)
    empty = MagicMock()
    empty.symbols = []
    empty.source = "empty"
    empty.detail = "no_watchlist"
    empty.to_dict.return_value = {"symbols": []}

    with (
        patch("api.server.get_watchlist", return_value=empty),
        patch("api.server.sniper_ticks_allowed", return_value=True),
        patch("api.server.entries_enabled", return_value=True),
        patch("api.server._holiday_noop", return_value=None),
    ):
        resp = client.post("/events", json={"phase": "tick", "force": True})
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert "empty watchlist" in body["detail"]


def test_premarket_builds_and_saves(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SECRET_ARN", "")
    from api import server

    client = TestClient(server.app)
    doc = MagicMock()
    doc.symbols = ["WXYZ"]
    doc.source = "fresh_scan"
    doc.detail = "candidates=1"
    doc.to_dict.return_value = {"symbols": ["WXYZ"]}

    with (
        patch("api.server.build_daily_watchlist", return_value=doc) as mock_build,
        patch("api.server.save_watchlist", return_value={"ok": True}) as mock_save,
        patch("api.server._holiday_noop", return_value=None),
    ):
        resp = client.post("/events", json={"phase": "premarket", "force": True})
    assert resp.status_code == 200
    mock_build.assert_called_once()
    mock_save.assert_called_once()
    assert "WXYZ" in resp.json()["detail"]


def test_postmarket_builds_watchlist(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SECRET_ARN", "")
    from api import server

    client = TestClient(server.app)
    doc = MagicMock()
    doc.symbols = ["LMNO"]
    doc.source = "fresh_scan"
    doc.detail = "candidates=1"
    doc.to_dict.return_value = {"symbols": ["LMNO"]}

    with (
        patch("api.server.build_daily_watchlist", return_value=doc),
        patch("api.server.save_watchlist", return_value={"ok": True}),
        patch("api.server.persist_journal_to_s3", return_value={"ok": True}),
        patch("api.server._holiday_noop", return_value=None),
    ):
        resp = client.post("/events", json={"phase": "postmarket", "force": True})
    assert resp.status_code == 200
    body = resp.json()
    assert body["results"][0]["tomorrow_watchlist"] == ["LMNO"]

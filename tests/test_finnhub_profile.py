"""Finnhub company profile → market cap USD for sniper routing."""

from __future__ import annotations

import json
from decimal import Decimal
from unittest.mock import MagicMock, patch

from trading_lab.catalysts.finnhub_profile import (
    clear_market_cap_cache,
    fetch_market_cap_usd,
)


def setup_function() -> None:
    clear_market_cap_cache()


def test_fetch_market_cap_converts_millions_to_usd(monkeypatch) -> None:
    monkeypatch.setenv("FINNHUB_API_KEY", "tok")
    payload = json.dumps({"ticker": "XYZ", "marketCapitalization": 5000}).encode()
    resp = MagicMock()
    resp.read.return_value = payload
    resp.__enter__ = lambda s: s
    resp.__exit__ = MagicMock(return_value=False)

    with patch("urllib.request.urlopen", return_value=resp) as mock_open:
        cap = fetch_market_cap_usd("xyz")

    assert cap == Decimal("5000000000")
    url = mock_open.call_args[0][0].full_url
    assert "stock/profile2" in url
    assert "symbol=XYZ" in url


def test_fetch_market_cap_missing_key_returns_none(monkeypatch) -> None:
    monkeypatch.delenv("FINNHUB_API_KEY", raising=False)
    assert fetch_market_cap_usd("XYZ") is None


def test_fetch_market_cap_empty_profile_returns_none(monkeypatch) -> None:
    monkeypatch.setenv("FINNHUB_API_KEY", "tok")
    resp = MagicMock()
    resp.read.return_value = b"{}"
    resp.__enter__ = lambda s: s
    resp.__exit__ = MagicMock(return_value=False)

    with patch("urllib.request.urlopen", return_value=resp):
        assert fetch_market_cap_usd("XYZ") is None


def test_fetch_market_cap_caches_within_ttl(monkeypatch) -> None:
    monkeypatch.setenv("FINNHUB_API_KEY", "tok")
    payload = json.dumps({"marketCapitalization": 2500}).encode()
    resp = MagicMock()
    resp.read.return_value = payload
    resp.__enter__ = lambda s: s
    resp.__exit__ = MagicMock(return_value=False)

    with patch("urllib.request.urlopen", return_value=resp) as mock_open:
        a = fetch_market_cap_usd("MID")
        b = fetch_market_cap_usd("MID")

    assert a == b == Decimal("2500000000")
    assert mock_open.call_count == 1

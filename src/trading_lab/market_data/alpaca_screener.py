"""Alpaca stock screener — movers + most-actives (no hardcoded symbols)."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from decimal import Decimal
from typing import Any


@dataclass(frozen=True)
class ScreenerRow:
    symbol: str
    source: str  # most_actives | gainer | loser
    price: Decimal | None = None
    volume: Decimal | None = None
    percent_change: Decimal | None = None
    trade_count: int | None = None


@dataclass(frozen=True)
class AssetMeta:
    symbol: str
    tradable: bool
    status: str
    asset_class: str
    exchange: str
    fractionable: bool = False
    name: str = ""


class AlpacaScreener:
    """Reads ALPACA_API_KEY / ALPACA_API_SECRET from environment."""

    def __init__(
        self,
        api_key: str | None = None,
        api_secret: str | None = None,
        data_url: str = "https://data.alpaca.markets",
        trade_url: str | None = None,
    ) -> None:
        self.api_key = api_key or os.environ.get("ALPACA_API_KEY", "")
        self.api_secret = api_secret or os.environ.get("ALPACA_API_SECRET", "")
        self.data_url = data_url.rstrip("/")
        paper = os.environ.get("ALPACA_PAPER", "true").lower() == "true"
        default_trade = (
            "https://paper-api.alpaca.markets" if paper else "https://api.alpaca.markets"
        )
        self.trade_url = (trade_url or default_trade).rstrip("/")

    def _headers(self) -> dict[str, str]:
        return {
            "APCA-API-KEY-ID": self.api_key,
            "APCA-API-SECRET-KEY": self.api_secret,
            "accept": "application/json",
        }

    def _get_json(self, url: str) -> dict[str, Any]:
        if not self.api_key or not self.api_secret:
            raise RuntimeError("Alpaca keys missing. Set ALPACA_API_KEY and ALPACA_API_SECRET.")
        req = urllib.request.Request(url, headers=self._headers())
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:  # nosec B310
                return json.loads(resp.read().decode())
        except urllib.error.HTTPError as exc:
            body = exc.read().decode(errors="replace")
            raise RuntimeError(f"Alpaca screener HTTP {exc.code}: {body}") from exc

    def most_actives(self, *, top: int = 25, by: str = "volume") -> list[ScreenerRow]:
        params = urllib.parse.urlencode({"by": by, "top": top})
        url = f"{self.data_url}/v1beta1/screener/stocks/most-actives?{params}"
        payload = self._get_json(url)
        rows: list[ScreenerRow] = []
        for item in payload.get("most_actives") or []:
            sym = str(item.get("symbol", "")).upper().strip()
            if not sym:
                continue
            rows.append(
                ScreenerRow(
                    symbol=sym,
                    source="most_actives",
                    volume=Decimal(str(item["volume"])) if item.get("volume") is not None else None,
                    trade_count=int(item["trade_count"])
                    if item.get("trade_count") is not None
                    else None,
                )
            )
        return rows

    def movers(self, *, top: int = 20) -> list[ScreenerRow]:
        params = urllib.parse.urlencode({"top": top})
        url = f"{self.data_url}/v1beta1/screener/stocks/movers?{params}"
        payload = self._get_json(url)
        rows: list[ScreenerRow] = []
        for label, key in (("gainer", "gainers"), ("loser", "losers")):
            for item in payload.get(key) or []:
                sym = str(item.get("symbol", "")).upper().strip()
                if not sym:
                    continue
                rows.append(
                    ScreenerRow(
                        symbol=sym,
                        source=label,
                        price=Decimal(str(item["price"]))
                        if item.get("price") is not None
                        else None,
                        percent_change=(
                            Decimal(str(item["percent_change"]))
                            if item.get("percent_change") is not None
                            else None
                        ),
                    )
                )
        return rows

    def last_trade_price(self, symbol: str, *, feed: str = "iex") -> Decimal | None:
        """Latest trade price; used when screener rows carry no price (most_actives)."""
        params = urllib.parse.urlencode({"feed": feed})
        url = (
            f"{self.data_url}/v2/stocks/{urllib.parse.quote(symbol.upper())}/trades/latest?{params}"
        )
        try:
            payload = self._get_json(url)
        except RuntimeError:
            return None
        trade = payload.get("trade") or {}
        px = trade.get("p")
        if px in (None, ""):
            return None
        try:
            price = Decimal(str(px))
        except Exception:  # noqa: BLE001
            return None
        return price if price > 0 else None

    def asset(self, symbol: str) -> AssetMeta | None:
        url = f"{self.trade_url}/v2/assets/{urllib.parse.quote(symbol.upper())}"
        try:
            payload = self._get_json(url)
        except RuntimeError:
            return None
        return AssetMeta(
            symbol=str(payload.get("symbol", symbol)).upper(),
            tradable=bool(payload.get("tradable", False)),
            status=str(payload.get("status", "")),
            asset_class=str(payload.get("class", payload.get("asset_class", ""))),
            exchange=str(payload.get("exchange", "")),
            fractionable=bool(payload.get("fractionable", False)),
            name=str(payload.get("name") or ""),
        )

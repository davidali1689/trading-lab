"""Alpaca historical bars — paper/data keys from env only (never hardcode)."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import timezone
from decimal import Decimal

from trading_lab.market_data.types import Bar, BarRequest


class AlpacaMarketData:
    """Reads ALPACA_API_KEY / ALPACA_API_SECRET from environment (Secrets Manager later)."""

    def __init__(
        self,
        api_key: str | None = None,
        api_secret: str | None = None,
        data_url: str = "https://data.alpaca.markets",
    ) -> None:
        self.api_key = api_key or os.environ.get("ALPACA_API_KEY", "")
        self.api_secret = api_secret or os.environ.get("ALPACA_API_SECRET", "")
        self.data_url = data_url.rstrip("/")

    def get_bars(self, request: BarRequest) -> list[Bar]:
        if not self.api_key or not self.api_secret:
            raise RuntimeError(
                "Alpaca keys missing. Set ALPACA_API_KEY and ALPACA_API_SECRET "
                "(paper keys only until promotion). Use MockMarketData for local runs."
            )
        # Alpaca v2 bars: /v2/stocks/{symbol}/bars
        params = urllib.parse.urlencode(
            {
                "timeframe": request.timeframe,
                "start": request.start.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
                "end": request.end.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
                "adjustment": "raw",
                "feed": request.feed,
                "limit": 10000,
            }
        )
        url = f"{self.data_url}/v2/stocks/{request.symbol}/bars?{params}"
        req = urllib.request.Request(
            url,
            headers={
                "APCA-API-KEY-ID": self.api_key,
                "APCA-API-SECRET-KEY": self.api_secret,
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:  # nosec B310
                payload = json.loads(resp.read().decode())
        except urllib.error.HTTPError as exc:
            body = exc.read().decode(errors="replace")
            raise RuntimeError(f"Alpaca bars HTTP {exc.code}: {body}") from exc

        out: list[Bar] = []
        for row in payload.get("bars") or []:
            from datetime import datetime

            out.append(
                Bar(
                    symbol=request.symbol,
                    ts=datetime.fromisoformat(row["t"].replace("Z", "+00:00")),
                    open=Decimal(str(row["o"])),
                    high=Decimal(str(row["h"])),
                    low=Decimal(str(row["l"])),
                    close=Decimal(str(row["c"])),
                    volume=Decimal(str(row["v"])),
                    vwap=Decimal(str(row["vw"])) if row.get("vw") is not None else None,
                    timeframe=request.timeframe,
                )
            )
        return out

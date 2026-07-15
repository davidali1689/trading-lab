"""Alpaca paper trading broker — orders against paper-api ($100k sim account).

Uses ALPACA_API_KEY / ALPACA_API_SECRET. Refuses live trading URL unless
TRADING_MODE=live and ALLOW_LIVE_KEYS=true.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from decimal import Decimal

from trading_lab.broker.types import BrokerAccount, BrokerOrderResult, BrokerPosition
from trading_lab.config.secrets import TradingMode, load_secrets
from trading_lab.schemas.trades import Side, TradeIntent

PAPER_BASE = "https://paper-api.alpaca.markets"
LIVE_BASE = "https://api.alpaca.markets"


class AlpacaPaperBroker:
    def __init__(
        self,
        api_key: str | None = None,
        api_secret: str | None = None,
        *,
        base_url: str | None = None,
    ) -> None:
        secrets = load_secrets()
        self.api_key = api_key or secrets.alpaca_api_key or os.environ.get("ALPACA_API_KEY", "")
        self.api_secret = (
            api_secret or secrets.alpaca_api_secret or os.environ.get("ALPACA_API_SECRET", "")
        )
        paper = secrets.alpaca_paper
        if base_url:
            self.base_url = base_url.rstrip("/")
        elif paper or secrets.trading_mode != TradingMode.LIVE:
            self.base_url = PAPER_BASE
        else:
            secrets.assert_paper_only()
            self.base_url = LIVE_BASE

        if not self.api_key or not self.api_secret:
            raise RuntimeError("Alpaca keys missing for paper broker")

    def _request(self, method: str, path: str, body: dict | None = None) -> dict | list:
        url = f"{self.base_url}{path}"
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(
            url,
            data=data,
            method=method,
            headers={
                "APCA-API-KEY-ID": self.api_key,
                "APCA-API-SECRET-KEY": self.api_secret,
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:  # nosec B310
                raw = resp.read().decode()
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as exc:
            err = exc.read().decode(errors="replace")
            raise RuntimeError(f"Alpaca {method} {path} HTTP {exc.code}: {err}") from exc

    def get_account(self) -> BrokerAccount:
        row = self._request("GET", "/v2/account")
        assert isinstance(row, dict)
        return BrokerAccount(
            equity=Decimal(str(row.get("equity") or "0")),
            cash=Decimal(str(row.get("cash") or "0")),
            buying_power=Decimal(str(row.get("buying_power") or "0")),
            paper=self.base_url.startswith(PAPER_BASE),
        )

    def get_open_positions(self) -> list[BrokerPosition]:
        rows = self._request("GET", "/v2/positions")
        if not isinstance(rows, list):
            return []
        out: list[BrokerPosition] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            out.append(
                BrokerPosition(
                    symbol=str(row.get("symbol") or ""),
                    qty=Decimal(str(row.get("qty") or "0")),
                    side=str(row.get("side") or ""),
                    market_value=Decimal(str(row.get("market_value") or "0")),
                    unrealized_pl=Decimal(str(row.get("unrealized_pl") or "0")),
                )
            )
        return out

    def has_open_position(self, symbol: str) -> bool:
        sym = symbol.upper()
        return any(p.symbol.upper() == sym and p.qty != 0 for p in self.get_open_positions())

    def submit_bracket_order(self, intent: TradeIntent) -> BrokerOrderResult:
        if intent.side != Side.LONG:
            raise RuntimeError("Alpaca paper broker v0 supports long entries only")
        if intent.stop_px is None or intent.target_px is None:
            raise RuntimeError("Bracket order requires stop_px and target_px")

        body = {
            "symbol": intent.symbol.upper(),
            "qty": str(int(intent.qty)),
            "side": "buy",
            "type": "market",
            "time_in_force": "day",
            "order_class": "bracket",
            "take_profit": {"limit_price": str(round(float(intent.target_px), 2))},
            "stop_loss": {"stop_price": str(round(float(intent.stop_px), 2))},
        }
        row = self._request("POST", "/v2/orders", body)
        assert isinstance(row, dict)
        return BrokerOrderResult(
            order_id=str(row.get("id") or ""),
            symbol=str(row.get("symbol") or intent.symbol),
            status=str(row.get("status") or ""),
            qty=Decimal(str(row.get("qty") or intent.qty)),
            raw=row,
        )

    def close_position(self, symbol: str) -> dict:
        """Market-close an open paper position (EOD flatten)."""
        row = self._request("DELETE", f"/v2/positions/{symbol.upper()}")
        return row if isinstance(row, dict) else {"result": row}

    def close_all_positions(self) -> list:
        """Close all open paper positions."""
        row = self._request("DELETE", "/v2/positions")
        return row if isinstance(row, list) else [row]

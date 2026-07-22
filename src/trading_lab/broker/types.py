"""Broker port — paper/live order submission (never invent fills)."""

from __future__ import annotations

from decimal import Decimal
from typing import Protocol

from pydantic import BaseModel, Field

from trading_lab.schemas.trades import TradeIntent


class BrokerAccount(BaseModel):
    equity: Decimal
    cash: Decimal
    buying_power: Decimal
    paper: bool = True
    # Settled / non-margin cash for T+1 swing discipline (Alpaca non_marginable_buying_power)
    settled_cash: Decimal | None = None


class BrokerPosition(BaseModel):
    symbol: str
    qty: Decimal
    side: str
    market_value: Decimal = Decimal("0")
    unrealized_pl: Decimal = Decimal("0")
    avg_entry_price: Decimal = Decimal("0")
    current_price: Decimal = Decimal("0")


class BrokerOrderResult(BaseModel):
    order_id: str
    symbol: str
    status: str
    qty: Decimal
    raw: dict = Field(default_factory=dict)
    filled_avg_price: Decimal | None = None


class BrokerPort(Protocol):
    def get_account(self) -> BrokerAccount: ...

    def get_open_positions(self) -> list[BrokerPosition]: ...

    def has_open_position(self, symbol: str) -> bool: ...

    def submit_bracket_order(self, intent: TradeIntent) -> BrokerOrderResult: ...

    def get_order(self, order_id: str) -> dict: ...

    def list_open_orders(self, symbol: str | None = None) -> list[dict]: ...

    def cancel_open_orders(self, symbol: str) -> list: ...

    def submit_oco_exit(
        self,
        *,
        symbol: str,
        qty: Decimal,
        stop_px: Decimal,
        target_px: Decimal,
        time_in_force: str = "gtc",
    ) -> BrokerOrderResult: ...

    def close_position(self, symbol: str, qty: Decimal | None = None) -> dict: ...

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


class BrokerPosition(BaseModel):
    symbol: str
    qty: Decimal
    side: str
    market_value: Decimal = Decimal("0")
    unrealized_pl: Decimal = Decimal("0")


class BrokerOrderResult(BaseModel):
    order_id: str
    symbol: str
    status: str
    qty: Decimal
    raw: dict = Field(default_factory=dict)


class BrokerPort(Protocol):
    def get_account(self) -> BrokerAccount: ...

    def get_open_positions(self) -> list[BrokerPosition]: ...

    def has_open_position(self, symbol: str) -> bool: ...

    def submit_bracket_order(self, intent: TradeIntent) -> BrokerOrderResult: ...

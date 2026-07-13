"""Resolve market-data backend from USE_MOCK_BARS."""

from __future__ import annotations

import os

from trading_lab.market_data.alpaca import AlpacaMarketData
from trading_lab.market_data.mock import MockMarketData
from trading_lab.market_data.types import MarketDataPort


def use_mock_bars() -> bool:
    return os.environ.get("USE_MOCK_BARS", "true").lower() in {"1", "true", "yes"}


def resolve_market_data() -> MarketDataPort:
    if use_mock_bars():
        return MockMarketData()
    return AlpacaMarketData()

"""Secrets — paper keys only from environment / future Secrets Manager.

Never commit live brokerage keys. LIVE mode requires explicit unlock.
"""

from __future__ import annotations

import os
from enum import StrEnum

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class TradingMode(StrEnum):
    BACKTEST = "backtest"
    SIM = "sim"
    PAPER = "paper"
    LIVE = "live"


class SecretsSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    alpaca_api_key: str = Field(default="", alias="ALPACA_API_KEY")
    alpaca_api_secret: str = Field(default="", alias="ALPACA_API_SECRET")
    alpaca_paper: bool = Field(default=True, alias="ALPACA_PAPER")
    finnhub_api_key: str = Field(default="", alias="FINNHUB_API_KEY")
    trading_mode: TradingMode = Field(default=TradingMode.BACKTEST, alias="TRADING_MODE")
    allow_live_keys: bool = Field(default=False, alias="ALLOW_LIVE_KEYS")

    def assert_paper_only(self) -> None:
        if self.trading_mode == TradingMode.LIVE and not self.allow_live_keys:
            raise RuntimeError(
                "LIVE blocked: set ALLOW_LIVE_KEYS=true only after journal evidence."
            )
        if not self.alpaca_paper and self.trading_mode != TradingMode.LIVE:
            raise RuntimeError("Non-paper Alpaca keys require TRADING_MODE=live + unlock.")


def load_secrets() -> SecretsSettings:
    return SecretsSettings()


def has_alpaca_keys() -> bool:
    return bool(os.environ.get("ALPACA_API_KEY") and os.environ.get("ALPACA_API_SECRET"))

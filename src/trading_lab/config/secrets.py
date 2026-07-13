"""Secrets — env/.env locally; AWS Secrets Manager in Lambda via SECRET_ARN.

Never commit live brokerage keys. LIVE mode requires explicit unlock.
"""

from __future__ import annotations

import json
import logging
import os
from enum import StrEnum
from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger("trading_lab.secrets")

_SECRET_ENV_KEYS = (
    "ALPACA_API_KEY",
    "ALPACA_API_SECRET",
    "ALPACA_PAPER",
    "FINNHUB_API_KEY",
    "UNUSUAL_WHALES_API_KEY",
    "GRAFANA_FEED_TOKEN",
)


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
    unusual_whales_api_key: str = Field(default="", alias="UNUSUAL_WHALES_API_KEY")
    trading_mode: TradingMode = Field(default=TradingMode.BACKTEST, alias="TRADING_MODE")
    allow_live_keys: bool = Field(default=False, alias="ALLOW_LIVE_KEYS")

    def assert_paper_only(self) -> None:
        if self.trading_mode == TradingMode.LIVE and not self.allow_live_keys:
            raise RuntimeError(
                "LIVE blocked: set ALLOW_LIVE_KEYS=true only after journal evidence."
            )
        if not self.alpaca_paper and self.trading_mode != TradingMode.LIVE:
            raise RuntimeError("Non-paper Alpaca keys require TRADING_MODE=live + unlock.")


def hydrate_env_from_secrets_manager(secret_arn: str | None = None) -> bool:
    """Load JSON secret into process env (does not overwrite non-empty env vars).

    Returns True if Secrets Manager was contacted successfully.
    """
    arn = secret_arn or os.environ.get("SECRET_ARN", "").strip()
    if not arn:
        return False
    try:
        import boto3
    except ImportError:
        logger.warning("boto3 missing — cannot load SECRET_ARN")
        return False

    try:
        client = boto3.client("secretsmanager")
        resp = client.get_secret_value(SecretId=arn)
        raw = resp.get("SecretString") or ""
        data = json.loads(raw) if raw else {}
    except Exception as exc:  # noqa: BLE001 — degrade to env/.env
        logger.warning("Secrets Manager load failed for %s: %s", arn, exc)
        return False

    if not isinstance(data, dict):
        logger.warning("Secret %s is not a JSON object", arn)
        return False

    for key in _SECRET_ENV_KEYS:
        value = data.get(key)
        if value is None or value == "":
            continue
        if os.environ.get(key):
            continue
        os.environ[key] = str(value)
    logger.info("Hydrated vendor keys from Secrets Manager")
    return True


@lru_cache(maxsize=1)
def load_secrets(*, hydrate: bool = True) -> SecretsSettings:
    if hydrate:
        hydrate_env_from_secrets_manager()
    return SecretsSettings()


def has_alpaca_keys() -> bool:
    load_secrets()
    return bool(os.environ.get("ALPACA_API_KEY") and os.environ.get("ALPACA_API_SECRET"))

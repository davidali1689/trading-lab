"""Tests for Secrets Manager hydration."""

from __future__ import annotations

import json
import os
from unittest.mock import MagicMock, patch

from trading_lab.config.secrets import (
    hydrate_env_from_secrets_manager,
    load_secrets,
)


def test_hydrate_skips_when_no_arn(monkeypatch):
    monkeypatch.delenv("SECRET_ARN", raising=False)
    assert hydrate_env_from_secrets_manager() is False


def test_hydrate_from_secret_json(monkeypatch):
    arn = "arn:aws:secretsmanager:us-east-1:123:secret:trading-lab-vendor-keys"
    monkeypatch.setenv("SECRET_ARN", arn)
    monkeypatch.delenv("ALPACA_API_KEY", raising=False)
    monkeypatch.delenv("UNUSUAL_WHALES_API_KEY", raising=False)

    payload = {
        "ALPACA_API_KEY": "PKTEST",
        "ALPACA_API_SECRET": "SECRET",
        "UNUSUAL_WHALES_API_KEY": "UWTEST",
    }
    client = MagicMock()
    client.get_secret_value.return_value = {"SecretString": json.dumps(payload)}

    with patch("boto3.client", return_value=client):
        assert hydrate_env_from_secrets_manager() is True

    assert os.environ["ALPACA_API_KEY"] == "PKTEST"
    assert os.environ["UNUSUAL_WHALES_API_KEY"] == "UWTEST"
    client.get_secret_value.assert_called_once()


def test_hydrate_does_not_overwrite_existing_env(monkeypatch):
    monkeypatch.setenv("SECRET_ARN", "arn:aws:secretsmanager:us-east-1:123:secret:x")
    monkeypatch.setenv("ALPACA_API_KEY", "LOCAL")
    client = MagicMock()
    client.get_secret_value.return_value = {
        "SecretString": json.dumps({"ALPACA_API_KEY": "FROM_SM"})
    }
    with patch("boto3.client", return_value=client):
        assert hydrate_env_from_secrets_manager() is True
    assert os.environ["ALPACA_API_KEY"] == "LOCAL"


def test_load_secrets_cached(monkeypatch):
    load_secrets.cache_clear()
    monkeypatch.delenv("SECRET_ARN", raising=False)
    monkeypatch.setenv("TRADING_MODE", "paper")
    s = load_secrets(hydrate=False)
    assert s.trading_mode.value == "paper"
    load_secrets.cache_clear()

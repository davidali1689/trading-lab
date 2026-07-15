"""Thin Bedrock converse wrapper — coach only, never entry decisions."""

from __future__ import annotations

import logging
import os

logger = logging.getLogger("trading_lab.improvement.bedrock")


def mock_bedrock_enabled() -> bool:
    return os.environ.get("MOCK_BEDROCK", "true").strip().lower() in {"1", "true", "yes"}


def bedrock_model_id() -> str:
    return os.environ.get("BEDROCK_MODEL_ID", "amazon.nova-lite-v1:0").strip()


class BedrockClient:
    """Converse API with MOCK_BEDROCK for CI/local."""

    def __init__(
        self,
        *,
        model_id: str | None = None,
        mock: bool | None = None,
        region: str | None = None,
    ) -> None:
        self.model_id = model_id or bedrock_model_id()
        self.mock = mock_bedrock_enabled() if mock is None else mock
        self.region = region or os.environ.get("AWS_REGION", "us-east-1")
        self._client = None

    @property
    def client(self):
        if self._client is None:
            import boto3

            self._client = boto3.client("bedrock-runtime", region_name=self.region)
        return self._client

    def converse(self, system: str, user_message: str, max_tokens: int = 1024) -> str:
        if self.mock:
            logger.info("MOCK_BEDROCK=true — stub narrative")
            return (
                f"[MOCK] Processed request with model {self.model_id}. "
                f"User message length: {len(user_message)} chars."
            )

        response = self.client.converse(
            modelId=self.model_id,
            messages=[
                {
                    "role": "user",
                    "content": [{"text": f"{system}\n\n---\n\n{user_message}"}],
                }
            ],
            inferenceConfig={"maxTokens": max_tokens, "temperature": 0.2},
        )
        return response["output"]["message"]["content"][0]["text"]

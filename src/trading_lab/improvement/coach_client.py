"""Coach model client — live Bedrock coaches (Kimi / Grok); mock for CI."""

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request
from typing import Any

from trading_lab.improvement.bedrock_client import mock_bedrock_enabled

logger = logging.getLogger("trading_lab.improvement.coach_client")


def coach_model_id() -> str:
    return os.environ.get("COACH_MODEL_ID", "moonshot.kimi-k2-thinking").strip()


def coach_fallback_model_id() -> str:
    return os.environ.get("COACH_FALLBACK_MODEL_ID", "amazon.nova-pro-v1:0").strip()


def coach_effort() -> str:
    return os.environ.get("COACH_REASONING_EFFORT", "high").strip().lower()


class CoachClient:
    """Friday strategy coaches. Never used for entries / order submit."""

    def __init__(
        self,
        *,
        model_id: str | None = None,
        mock: bool | None = None,
        region: str | None = None,
    ) -> None:
        self.model_id = model_id or coach_model_id()
        self.fallback_model_id = coach_fallback_model_id()
        self.mock = mock_bedrock_enabled() if mock is None else mock
        self.region = (
            region
            or os.environ.get("COACH_AWS_REGION")
            or os.environ.get("AWS_REGION", "us-east-1")
        )
        self.effort = coach_effort()

    def analyze(self, system: str, user_message: str, *, max_tokens: int = 2048) -> str:
        if self.mock:
            logger.info("MOCK_BEDROCK=true — stub coach narrative model=%s", self.model_id)
            return (
                f"[MOCK coach {self.model_id} effort={self.effort}] "
                f"Review miss harvest; propose bounded gate/watchlist tweaks. "
                f"User payload chars={len(user_message)}."
            )
        try:
            return self._invoke(self.model_id, system, user_message, max_tokens=max_tokens)
        except Exception as exc:  # noqa: BLE001
            if not self.fallback_model_id or self.fallback_model_id == self.model_id:
                raise
            logger.warning(
                "coach model %s failed (%s); trying fallback %s",
                self.model_id,
                exc,
                self.fallback_model_id,
            )
            text = self._invoke(
                self.fallback_model_id,
                system,
                user_message,
                max_tokens=max_tokens,
            )
            self.model_id = self.fallback_model_id
            return text

    def _invoke(
        self,
        model_id: str,
        system: str,
        user_message: str,
        *,
        max_tokens: int,
    ) -> str:
        if model_id.startswith("xai."):
            try:
                return self._mantle_chat(model_id, system, user_message, max_tokens=max_tokens)
            except Exception as exc:  # noqa: BLE001
                logger.warning("mantle chat failed, trying converse: %s", exc)
        return self._converse(model_id, system, user_message, max_tokens=max_tokens)

    def _converse(
        self,
        model_id: str,
        system: str,
        user_message: str,
        *,
        max_tokens: int,
    ) -> str:
        import boto3

        client = boto3.client("bedrock-runtime", region_name=self.region)
        try:
            response = client.converse(
                modelId=model_id,
                messages=[
                    {
                        "role": "user",
                        "content": [{"text": f"{system}\n\n---\n\n{user_message}"}],
                    }
                ],
                inferenceConfig={"maxTokens": max_tokens, "temperature": 0.2},
            )
        except Exception as exc:  # noqa: BLE001
            # Bedrock often requires the regional inference-profile prefix;
            # 2026-08-04 W31 coaches died on ValidationException for the bare ID.
            if "model identifier" in str(exc) and not model_id.startswith("us."):
                prefixed = f"us.{model_id}"
                logger.warning("converse model %s invalid; retrying as %s", model_id, prefixed)
                response = client.converse(
                    modelId=prefixed,
                    messages=[
                        {
                            "role": "user",
                            "content": [{"text": f"{system}\n\n---\n\n{user_message}"}],
                        }
                    ],
                    inferenceConfig={"maxTokens": max_tokens, "temperature": 0.2},
                )
                self.model_id = prefixed
            else:
                raise
        return response["output"]["message"]["content"][0]["text"]

    def _mantle_chat(
        self,
        model_id: str,
        system: str,
        user_message: str,
        *,
        max_tokens: int,
    ) -> str:
        """SigV4 POST to bedrock-mantle OpenAI chat completions."""
        import boto3
        from botocore.auth import SigV4Auth
        from botocore.awsrequest import AWSRequest

        url = f"https://bedrock-mantle.{self.region}.api.aws/openai/v1/chat/completions"
        payload: dict[str, Any] = {
            "model": model_id,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user_message},
            ],
            "max_tokens": max_tokens,
            "temperature": 0.2,
        }
        if self.effort in {"none", "low", "medium", "high"}:
            payload["reasoning_effort"] = self.effort

        body = json.dumps(payload).encode("utf-8")
        session = boto3.Session()
        creds = session.get_credentials()
        if creds is None:
            raise RuntimeError("no AWS credentials for Mantle")
        frozen = creds.get_frozen_credentials()
        request = AWSRequest(
            method="POST",
            url=url,
            data=body,
            headers={"Content-Type": "application/json", "Accept": "application/json"},
        )
        SigV4Auth(frozen, "bedrock", self.region).add_auth(request)
        prepared = request.prepare()
        req = urllib.request.Request(
            url,
            data=body,
            headers=dict(prepared.headers),
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:  # nosec B310
                data = json.loads(resp.read().decode())
        except urllib.error.HTTPError as exc:
            err = exc.read().decode(errors="replace")
            raise RuntimeError(f"Mantle HTTP {exc.code}: {err}") from exc
        choices = data.get("choices") or []
        if not choices:
            raise RuntimeError(f"Mantle empty choices: {data!r}")
        message = choices[0].get("message") or {}
        content = message.get("content")
        if isinstance(content, list):
            texts = [p.get("text", "") for p in content if isinstance(p, dict)]
            return "\n".join(t for t in texts if t)
        return str(content or "")

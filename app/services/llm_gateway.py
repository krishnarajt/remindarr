"""Thin client for the internal LLM gateway.

Contract (see the LLMGateway service):
    POST {base}/api/chat   header: X-API-Key: <key>
    body: {system_prompt, user_prompt, config:{model, temperature,
           max_output_tokens, thinking, extra}}
    resp: {content, model, provider, usage, thinking}

The generated text is returned as a string in ``content`` — structured output is
parsed and validated by the caller.
"""

from __future__ import annotations

from typing import Any, Optional

import httpx

from app.common.constants import settings
from app.utils.logging_utils import logger


class LLMGatewayError(RuntimeError):
    pass


class LLMGatewayClient:
    def __init__(
        self,
        base_url: Optional[str] = None,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
    ):
        self.base_url = (base_url or settings.llm_gateway_url).rstrip("/")
        self.api_key = api_key or settings.llm_gateway_api_key
        self.model = model or settings.llm_model
        # Gateway times out LLM calls at ~120s; match it.
        self._client = httpx.Client(timeout=httpx.Timeout(125.0, connect=10.0))

    @property
    def configured(self) -> bool:
        return bool(self.api_key)

    def chat(
        self,
        user_prompt: str,
        *,
        system_prompt: Optional[str] = None,
        temperature: Optional[float] = None,
        max_output_tokens: Optional[int] = None,
        extra: Optional[dict[str, Any]] = None,
    ) -> str:
        """Send a single-turn completion; return the model's text content."""
        if not self.configured:
            raise LLMGatewayError("LLM gateway API key is not configured")

        config: dict[str, Any] = {"model": self.model}
        if temperature is not None:
            config["temperature"] = temperature
        if max_output_tokens is not None:
            config["max_output_tokens"] = max_output_tokens
        if extra:
            config["extra"] = extra

        payload = {
            "system_prompt": system_prompt,
            "user_prompt": user_prompt,
            "config": config,
        }
        try:
            resp = self._client.post(
                f"{self.base_url}/api/chat",
                json=payload,
                headers={"X-API-Key": self.api_key},
            )
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            logger.error("LLM gateway %s: %s", exc.response.status_code, exc.response.text)
            raise LLMGatewayError(f"gateway returned {exc.response.status_code}") from exc
        except Exception as exc:  # noqa: BLE001
            logger.error("LLM gateway request failed: %s", exc)
            raise LLMGatewayError(str(exc)) from exc

        data = resp.json()
        content = data.get("content")
        if not content:
            raise LLMGatewayError("gateway response had no content")
        return content


# Shared instance built from settings.
gateway = LLMGatewayClient()

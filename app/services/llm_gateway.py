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

import time
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
        chat_path: Optional[str] = None,
        max_attempts: int = 3,
    ):
        self.base_url = (base_url or settings.llm_gateway_url).rstrip("/")
        self.api_key = api_key or settings.llm_gateway_api_key
        self.model = model or settings.llm_model
        configured_path = chat_path or settings.llm_gateway_chat_path
        self.chat_path = "/" + configured_path.strip("/")
        self.max_attempts = max(1, max_attempts)
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
        url = f"{self.base_url}{self.chat_path}"
        resp: Optional[httpx.Response] = None
        for attempt in range(1, self.max_attempts + 1):
            try:
                resp = self._client.post(
                    url,
                    json=payload,
                    headers={"Content-Type": "application/json", "X-API-Key": self.api_key},
                )
                resp.raise_for_status()
                break
            except httpx.HTTPStatusError as exc:
                status = exc.response.status_code
                detail = exc.response.text[:500]
                retryable = status in {408, 429} or status >= 500
                logger.warning(
                    "LLM gateway attempt %d/%d returned %s: %s",
                    attempt,
                    self.max_attempts,
                    status,
                    detail,
                )
                if not retryable or attempt == self.max_attempts:
                    raise LLMGatewayError(f"gateway returned {status}: {detail}") from exc
            except httpx.RequestError as exc:
                logger.warning(
                    "LLM gateway attempt %d/%d failed: %s",
                    attempt,
                    self.max_attempts,
                    exc,
                )
                if attempt == self.max_attempts:
                    raise LLMGatewayError(f"gateway request failed: {exc}") from exc

            # Short bounded backoff: enough for a gateway/provider hiccup without
            # making Telegram users wait through another full request timeout.
            time.sleep(0.25 * (2 ** (attempt - 1)))

        if resp is None:  # Defensive; every loop exit above sets or raises.
            raise LLMGatewayError("gateway request did not produce a response")
        try:
            data = resp.json()
        except (ValueError, TypeError) as exc:
            raise LLMGatewayError("gateway returned invalid JSON") from exc
        if not isinstance(data, dict):
            raise LLMGatewayError("gateway returned an invalid response object")
        content = data.get("content")
        if not isinstance(content, str) or not content.strip():
            raise LLMGatewayError("gateway response had no content")
        return content.strip()


# Shared instance built from settings.
gateway = LLMGatewayClient()

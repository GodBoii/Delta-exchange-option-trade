"""Preserve bounded model-provider failure details even when Agno returns an error run."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import httpx
from agno.exceptions import ModelProviderError
from openai import AsyncOpenAI, DefaultAsyncHttpxClient

from .model import EvidenceOpenRouter


async def _normalize_embedded_error(response: httpx.Response) -> None:
    """Let the SDK apply one retry budget to HTTP and buffered OpenRouter errors."""
    if response.status_code != 200 or "application/json" not in response.headers.get("content-type", ""):
        return
    await response.aread()
    try:
        body = response.json()
    except ValueError:
        return
    error = body.get("error") if isinstance(body, dict) else None
    status = error.get("code") if isinstance(error, dict) else None
    if type(status) is int and 400 <= status <= 599:
        response.status_code = status


def provider_failure(error: Exception, api_key: str | None = None) -> dict:
    message = str(error)
    if isinstance(error, (TimeoutError, httpx.TimeoutException)) and not message:
        message = "News request timed out"
    if api_key:
        message = message.replace(api_key, "[redacted]")
    message = re.sub(r"Bearer\s+\S+|sk-[\w-]+", "[redacted]", message, flags=re.IGNORECASE)
    message = re.sub(r"https://openrouter\.ai/[^\s]*keys/[^\s]+", "[provider key settings]", message)
    status = getattr(error, "status_code", None)
    code = "model_provider_failed"
    if re.search(r"credit|key limit|insufficient funds|spending limit", message, re.IGNORECASE):
        code = "model_credit_limit"
    elif status == 429:
        code = "model_rate_limited"
    elif (isinstance(error, (TimeoutError, httpx.TimeoutException))
          or re.search(r"timed? out|timeout", message, re.IGNORECASE)):
        code = "model_timeout"
    return {"code": code, "stage": "news", "http_status": status, "message": message[:300]}


@dataclass
class ResearchOpenRouter(EvidenceOpenRouter):
    last_failure: dict | None = field(default=None, init=False, repr=False)

    def get_async_client(self) -> AsyncOpenAI:
        if self.http_client is None:
            self.http_client = DefaultAsyncHttpxClient()
        if isinstance(self.http_client, httpx.AsyncClient):
            hooks = self.http_client.event_hooks["response"]
            if _normalize_embedded_error not in hooks:
                hooks.append(_normalize_embedded_error)
        return super().get_async_client()

    async def ainvoke(self, *args, **kwargs):
        try:
            return await super().ainvoke(*args, **kwargs)
        except ModelProviderError as error:
            self.last_failure = provider_failure(error, self.api_key)
            raise


class NewsResearchError(RuntimeError):
    def __init__(self, failure: dict, trace: list[dict]) -> None:
        super().__init__(failure.get("message") or "News synthesis returned no report")
        self.failure = failure
        self.trace = trace

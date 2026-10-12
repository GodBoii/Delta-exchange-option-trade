"""Preserve bounded model-provider failure details even when Agno returns an error run."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from agno.exceptions import ModelProviderError
from agno.models.openrouter import OpenRouter


def provider_failure(error: Exception, api_key: str | None = None) -> dict:
    message = str(error)
    if api_key:
        message = message.replace(api_key, "[redacted]")
    message = re.sub(r"Bearer\s+\S+|sk-[\w-]+", "[redacted]", message, flags=re.IGNORECASE)
    status = getattr(error, "status_code", None)
    code = "model_provider_failed"
    if re.search(r"credit|key limit|insufficient funds|spending limit", message, re.IGNORECASE):
        code = "model_credit_limit"
    elif status == 429:
        code = "model_rate_limited"
    elif re.search(r"timed? out|timeout", message, re.IGNORECASE):
        code = "model_timeout"
    return {"code": code, "stage": "news", "http_status": status, "message": message[:300]}


@dataclass
class ResearchOpenRouter(OpenRouter):
    last_failure: dict | None = field(default=None, init=False, repr=False)

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

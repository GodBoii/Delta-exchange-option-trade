from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

BACKEND_DIR = Path(__file__).resolve().parents[1]
ENV_FILE = BACKEND_DIR / ".env"


def _env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return max(minimum, min(maximum, value))


def _csv_env(name: str) -> tuple[str, ...]:
    return tuple(value.strip().lower() for value in os.getenv(name, "").split(",") if value.strip())


@dataclass(frozen=True, slots=True)
class NewsAgentSettings:
    openrouter_api_key: str | None
    model_id: str
    app_name: str
    http_referer: str | None
    search_backend: str
    search_region: str
    search_timeout_seconds: int
    article_timeout_seconds: int
    max_article_chars: int
    max_download_bytes: int
    max_redirects: int
    allowed_domains: tuple[str, ...]

    @classmethod
    def load(cls) -> NewsAgentSettings:
        load_dotenv(ENV_FILE, override=False)
        return cls(
            openrouter_api_key=os.getenv("OPENROUTER_API_KEY") or None,
            model_id=os.getenv("NEWS_AGENT_MODEL", "poolside/laguna-xs-2.1:free"),
            app_name=os.getenv("NEWS_AGENT_APP_NAME", "Delta News Intelligence Prototype"),
            http_referer=os.getenv("NEWS_AGENT_HTTP_REFERER") or None,
            search_backend=os.getenv("NEWS_AGENT_SEARCH_BACKEND", "auto"),
            search_region=os.getenv("NEWS_AGENT_SEARCH_REGION", "wt-wt"),
            search_timeout_seconds=_env_int("NEWS_AGENT_SEARCH_TIMEOUT_SECONDS", 12, 3, 60),
            article_timeout_seconds=_env_int("NEWS_AGENT_ARTICLE_TIMEOUT_SECONDS", 15, 3, 60),
            max_article_chars=_env_int("NEWS_AGENT_MAX_ARTICLE_CHARS", 20_000, 2_000, 60_000),
            max_download_bytes=_env_int("NEWS_AGENT_MAX_DOWNLOAD_BYTES", 2_000_000, 100_000, 8_000_000),
            max_redirects=_env_int("NEWS_AGENT_MAX_REDIRECTS", 3, 0, 5),
            allowed_domains=_csv_env("NEWS_AGENT_ALLOWED_DOMAINS"),
        )

    def require_api_key(self) -> str:
        if not self.openrouter_api_key:
            raise RuntimeError(
                f"OPENROUTER_API_KEY is missing. Add it to {ENV_FILE} before running the news agent."
            )
        return self.openrouter_api_key

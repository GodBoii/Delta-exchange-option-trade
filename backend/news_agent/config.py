from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

BACKEND_DIR = Path(__file__).resolve().parents[1]
ENV_FILE = BACKEND_DIR / ".env"


def _csv_env(name: str) -> tuple[str, ...]:
    return tuple(value.strip().lower() for value in os.getenv(name, "").split(",") if value.strip())


@dataclass(frozen=True, slots=True)
class NewsAgentSettings:
    openrouter_api_key: str | None
    model_id: str
    allowed_domains: tuple[str, ...]
    supabase_db_url: str | None
    default_session_id: str
    default_user_id: str

    @classmethod
    def load(cls) -> NewsAgentSettings:
        load_dotenv(ENV_FILE, override=False)
        return cls(
            openrouter_api_key=os.getenv("OPENROUTER_API_KEY") or None,
            model_id=os.getenv("NEWS_AGENT_MODEL", "poolside/laguna-xs-2.1:free"),
            allowed_domains=_csv_env("NEWS_AGENT_ALLOWED_DOMAINS"),
            supabase_db_url=os.getenv("SUPABASE_DB_URL") or None,
            default_session_id=os.getenv("NEWS_AGENT_DEFAULT_SESSION_ID", "news-research-default"),
            default_user_id=os.getenv("NEWS_AGENT_DEFAULT_USER_ID", "local-user"),
        )

    def require_api_key(self) -> str:
        if not self.openrouter_api_key:
            raise RuntimeError(
                f"OPENROUTER_API_KEY is missing. Add it to {ENV_FILE} before running the news agent."
            )
        return self.openrouter_api_key

    def require_database_url(self) -> str:
        if not self.supabase_db_url:
            raise RuntimeError(
                "SUPABASE_DB_URL is missing. Copy the PostgreSQL connection URI from Supabase Connect "
                "and use the postgresql+psycopg:// scheme."
            )
        if self.supabase_db_url.startswith("postgres://"):
            return self.supabase_db_url.replace("postgres://", "postgresql+psycopg://", 1)
        if self.supabase_db_url.startswith("postgresql://"):
            return self.supabase_db_url.replace("postgresql://", "postgresql+psycopg://", 1)
        return self.supabase_db_url

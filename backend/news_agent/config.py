from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from dotenv import load_dotenv

BACKEND_DIR = Path(__file__).resolve().parents[1]
ENV_FILE = BACKEND_DIR / ".env"

MODEL_ID = "deepseek/deepseek-v4-flash-0731"
AUTOMATION_MODEL_ID = "deepseek/deepseek-v4-flash-vision-exp"
AUTOMATION_SESSION_TABLE = "automation_agent_sessions"
SESSION_TABLE = "news_agent_sessions"
DB_SCHEMA = "ai"
DB_CREATE_SCHEMA = True
HISTORY_RUNS = 15
DEFAULT_SESSION_ID = "news-research-default"
DEFAULT_USER_ID = "local-user"
ALLOWED_DOMAINS: tuple[str, ...] = ()
AUTOMATION_CHART_BUCKET = "automation-charts"
CHART_SIGNED_URL_SECONDS = 3_600


def _with_connection_defaults(url: str) -> str:
    parts = urlsplit(url)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query.setdefault("sslmode", "require")
    query.setdefault("keepalives", "1")
    query.setdefault("keepalives_idle", "30")
    query.setdefault("keepalives_interval", "10")
    query.setdefault("keepalives_count", "5")
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))


@dataclass(frozen=True, slots=True)
class NewsAgentSettings:
    openrouter_api_key: str | None
    model_id: str
    automation_model_id: str
    allowed_domains: tuple[str, ...]
    supabase_db_url: str | None
    session_table: str
    db_schema: str
    db_create_schema: bool
    history_runs: int | None
    default_session_id: str
    default_user_id: str
    supabase_url: str | None
    supabase_service_role_key: str | None
    automation_chart_bucket: str = AUTOMATION_CHART_BUCKET
    chart_signed_url_seconds: int = CHART_SIGNED_URL_SECONDS
    automation_session_table: str = AUTOMATION_SESSION_TABLE

    @classmethod
    def load(cls) -> NewsAgentSettings:
        load_dotenv(ENV_FILE, override=False)
        return cls(
            openrouter_api_key=os.getenv("OPENROUTER_API_KEY") or None,
            model_id=MODEL_ID,
            automation_model_id=AUTOMATION_MODEL_ID,
            allowed_domains=ALLOWED_DOMAINS,
            supabase_db_url=os.getenv("SUPABASE_DB_URL") or None,
            session_table=SESSION_TABLE,
            db_schema=DB_SCHEMA,
            db_create_schema=DB_CREATE_SCHEMA,
            history_runs=HISTORY_RUNS,
            default_session_id=DEFAULT_SESSION_ID,
            default_user_id=DEFAULT_USER_ID,
            supabase_url=os.getenv("NEXT_PUBLIC_SUPABASE_URL") or None,
            supabase_service_role_key=os.getenv("SUPABASE_SERVICE_ROLE_KEY") or None,
        )

    def require_api_key(self) -> str:
        if not self.openrouter_api_key:
            raise RuntimeError(f"OPENROUTER_API_KEY is missing. Add it to {ENV_FILE} before running the news agent.")
        return self.openrouter_api_key

    def require_database_url(self) -> str:
        if not self.supabase_db_url:
            raise RuntimeError(
                "SUPABASE_DB_URL is missing. Copy the PostgreSQL connection URI from Supabase Connect "
                "and use the postgresql+psycopg:// scheme."
            )
        db_url = self.supabase_db_url
        if db_url.startswith("postgres://"):
            db_url = db_url.replace("postgres://", "postgresql+psycopg://", 1)
        elif db_url.startswith("postgresql://"):
            db_url = db_url.replace("postgresql://", "postgresql+psycopg://", 1)
        elif not db_url.startswith("postgresql+psycopg://"):
            raise RuntimeError("SUPABASE_DB_URL must use a PostgreSQL connection URI")

        try:
            db_parts = urlsplit(db_url)
            _ = db_parts.port
        except ValueError as exc:
            raise RuntimeError(
                "SUPABASE_DB_URL is malformed. Percent-encode special characters in the database password."
            ) from exc
        if not all((db_parts.hostname, db_parts.username, db_parts.password, db_parts.path.strip("/"))):
            raise RuntimeError("SUPABASE_DB_URL must include a host, username, password, and database name")

        return _with_connection_defaults(db_url)

    def require_storage(self) -> tuple[str, str]:
        if not self.supabase_url or not self.supabase_service_role_key:
            raise RuntimeError(
                "Supabase Storage is not configured for automation charts. Provide NEXT_PUBLIC_SUPABASE_URL and "
                "SUPABASE_SERVICE_ROLE_KEY to the news-analyzer service."
            )
        return self.supabase_url.rstrip("/"), self.supabase_service_role_key

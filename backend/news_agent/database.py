from agno.db.base import BaseDb
from agno.db.postgres import PostgresDb

from .config import NewsAgentSettings


def create_session_db(settings: NewsAgentSettings | None = None) -> BaseDb:
    """Create the Agno session database backed directly by Supabase PostgreSQL."""
    settings = settings or NewsAgentSettings.load()
    return PostgresDb(db_url=settings.require_database_url())

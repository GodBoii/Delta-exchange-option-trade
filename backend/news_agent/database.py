from agno.db.base import BaseDb
from agno.db.postgres import PostgresDb
from sqlalchemy import text

from .config import NewsAgentSettings


def create_session_db(settings: NewsAgentSettings | None = None, *, session_table: str | None = None) -> BaseDb:
    """Create the Agno session database backed directly by Supabase PostgreSQL."""
    settings = settings or NewsAgentSettings.load()
    return PostgresDb(
        db_url=settings.require_database_url(),
        db_schema=settings.db_schema,
        session_table=session_table or settings.session_table,
        create_schema=settings.db_create_schema,
    )


def verify_session_db(db: BaseDb) -> None:
    """Fail fast when the lazy SQLAlchemy engine cannot authenticate or connect."""
    engine = getattr(db, "db_engine", None)
    if engine is None:
        raise RuntimeError("The configured Agno database does not expose a PostgreSQL engine")
    with engine.connect() as connection:
        connection.execute(text("SELECT 1"))

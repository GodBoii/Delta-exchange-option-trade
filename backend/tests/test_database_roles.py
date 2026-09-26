"""The analysis role reaches research tables only; the reader role only reads."""

import os
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

import psycopg
import pytest

from scripts.database_roles import ensure_roles

pytestmark = pytest.mark.skipif(
    not os.getenv("TEST_LOCAL_DATABASE_URL"), reason="No isolated local PostgreSQL test URL"
)


def role_url(writer: str, user: str, password: str) -> str:
    parts = urlsplit(writer)
    host = parts.hostname + (f":{parts.port}" if parts.port else "")
    return urlunsplit((parts.scheme, f"{user}:{password}@{host}", parts.path, parts.query, ""))


def test_roles_are_least_privilege_and_password_rotation_applies():
    writer = os.environ["TEST_LOCAL_DATABASE_URL"]
    ai = role_url(writer, "test_ai_role", "first-password")
    reader = role_url(writer, "test_reader_role", "reader-password")
    assert ensure_roles(writer, ai_url=ai, reader_url=reader) == ["test_ai_role", "test_reader_role"]

    run_id = str(uuid4())
    with psycopg.connect(ai) as connection:
        connection.execute("insert into ai.analysis_reports (id) values (%s)", (run_id,))
        connection.execute("create table if not exists ai.test_agent_sessions (session_id text primary key)")
        connection.commit()
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            connection.execute("select count(*) from trade.strategies")

    with psycopg.connect(reader) as connection:
        assert connection.execute("select count(*) from ai.analysis_reports where id=%s", (run_id,)).fetchone()[0]
        connection.execute("select count(*) from trade.strategies")
        with pytest.raises(psycopg.errors.ReadOnlySqlTransaction):
            connection.execute("delete from ai.analysis_reports where id=%s", (run_id,))

    rotated = role_url(writer, "test_ai_role", "second-password")
    ensure_roles(writer, ai_url=rotated)
    with psycopg.connect(rotated) as connection:
        connection.execute("drop table ai.test_agent_sessions")
    with pytest.raises(psycopg.OperationalError):
        psycopg.connect(ai).close()


def test_roles_must_share_the_writer_database_and_differ_from_it():
    writer = os.environ["TEST_LOCAL_DATABASE_URL"]
    with pytest.raises(ValueError, match="writer's database"):
        ensure_roles(writer, ai_url=role_url(writer, "x", "y").replace("/trade_cognition", "/other"))
    with pytest.raises(ValueError, match="differ"):
        ensure_roles(writer, ai_url=writer)

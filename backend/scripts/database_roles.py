"""Create or update the least-privilege PostgreSQL roles that share the application database.

* The analysis role (``AI_DATABASE_URL``) owns Agno session tables and can read and write
  ``ai.*``. It has no access to trading tables; trading changes go through the writer API.
* The reader role (``LOCAL_READER_DATABASE_URL``) can only read, for API read replicas.

The trading writer applies this at startup, so rotating a password in the environment
and restarting the writer is enough to update the role.
"""

import argparse
import os
from urllib.parse import unquote, urlsplit

import psycopg
from psycopg import sql


def _role(writer_url: str, role_url: str, label: str) -> tuple[str, str]:
    writer, role = urlsplit(writer_url), urlsplit(role_url)
    if (writer.hostname, writer.port, writer.path) != (role.hostname, role.port, role.path):
        raise ValueError(f"The {label} role must use the writer's database")
    if not role.username or not role.password:
        raise ValueError(f"The {label} URL requires a user and password")
    if unquote(role.username) == unquote(writer.username or ""):
        raise ValueError(f"The {label} role must differ from the writer role")
    return unquote(role.username), unquote(role.password)


def _login_role(connection: psycopg.Connection, name: str, password: str) -> sql.Identifier:
    role = sql.Identifier(name)
    exists = connection.execute("select 1 from pg_roles where rolname=%s", (name,)).fetchone()
    statement = "alter role {} with login password {}" if exists else "create role {} with login password {}"
    connection.execute(sql.SQL(statement).format(role, sql.Literal(password)))
    database = connection.info.dbname
    connection.execute(sql.SQL("grant connect on database {} to {}").format(sql.Identifier(database), role))
    return role


def ensure_roles(writer_url: str, *, ai_url: str | None = None, reader_url: str | None = None) -> list[str]:
    updated = []
    with psycopg.connect(writer_url) as connection:
        connection.execute("select pg_advisory_xact_lock(hashtext('trade-database-roles'))")
        writer = sql.Identifier(connection.execute("select current_user").fetchone()[0])
        if ai_url:
            name, password = _role(writer_url, ai_url, "analysis")
            role = _login_role(connection, name, password)
            connection.execute(sql.SQL("grant usage, create on schema ai to {}").format(role))
            connection.execute(
                sql.SQL("grant select, insert, update, delete on all tables in schema ai to {}").format(role)
            )
            connection.execute(
                sql.SQL(
                    "alter default privileges for role {} in schema ai "
                    "grant select, insert, update, delete on tables to {}"
                ).format(writer, role)
            )
            updated.append(name)
        if reader_url:
            name, password = _role(writer_url, reader_url, "reader")
            role = _login_role(connection, name, password)
            connection.execute(sql.SQL("alter role {} set default_transaction_read_only = on").format(role))
            for schema in ("trade", "ai"):
                target = sql.Identifier(schema)
                connection.execute(sql.SQL("grant usage on schema {} to {}").format(target, role))
                connection.execute(sql.SQL("grant select on all tables in schema {} to {}").format(target, role))
                connection.execute(
                    sql.SQL("alter default privileges for role {} in schema {} grant select on tables to {}").format(
                        writer, target, role
                    )
                )
            updated.append(name)
    return updated


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.parse_args()
    writer = os.getenv("LOCAL_DATABASE_URL")
    if not writer:
        raise ValueError("LOCAL_DATABASE_URL is required")
    for name in ensure_roles(
        writer, ai_url=os.getenv("AI_DATABASE_URL"), reader_url=os.getenv("LOCAL_READER_DATABASE_URL")
    ):
        print(f"Role ready: {name}")


if __name__ == "__main__":
    main()

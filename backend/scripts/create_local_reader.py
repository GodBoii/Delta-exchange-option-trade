"""Create a database role limited to reading Trade Cognition tables."""

import os
from urllib.parse import unquote, urlsplit

import psycopg
from psycopg import sql


def main() -> None:
    writer = os.getenv("LOCAL_DATABASE_URL")
    reader = os.getenv("LOCAL_READER_DATABASE_URL")
    if not writer or not reader:
        raise ValueError("LOCAL_DATABASE_URL and LOCAL_READER_DATABASE_URL are required")
    writer_url, reader_url = urlsplit(writer), urlsplit(reader)
    if (writer_url.hostname, writer_url.port, writer_url.path) != (
        reader_url.hostname,
        reader_url.port,
        reader_url.path,
    ):
        raise ValueError("Reader and writer must use the same local database")
    if not reader_url.username or not reader_url.password:
        raise ValueError("Reader URL requires a distinct user and password")
    if writer_url.username == reader_url.username:
        raise ValueError("Reader and writer must use distinct roles")
    role = unquote(reader_url.username)
    password = unquote(reader_url.password)
    with psycopg.connect(writer) as connection:
        exists = connection.execute("select 1 from pg_roles where rolname=%s", (role,)).fetchone()
        if exists:
            connection.execute(
                sql.SQL("alter role {} with login password {}").format(sql.Identifier(role), sql.Literal(password))
            )
        else:
            connection.execute(
                sql.SQL("create role {} with login password {}").format(sql.Identifier(role), sql.Literal(password))
            )
        connection.execute(sql.SQL("alter role {} set default_transaction_read_only = on").format(sql.Identifier(role)))
        connection.execute(sql.SQL("grant usage on schema trade to {}").format(sql.Identifier(role)))
        connection.execute(sql.SQL("grant select on all tables in schema trade to {}").format(sql.Identifier(role)))
        connection.execute(
            sql.SQL("alter default privileges in schema trade grant select on tables to {}").format(
                sql.Identifier(role)
            )
        )
    print("Local reader role is ready")


if __name__ == "__main__":
    main()

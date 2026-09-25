"""Persist research evidence in PostgreSQL before referencing it from Convex."""

import json
from typing import Any
from uuid import uuid4

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from app.application_data import ConvexApplicationData

from .local_client import LocalResearchClient


class ResearchData(ConvexApplicationData):
    def __init__(
        self, url: str, secret: str, database_url: str, *, local_client: LocalResearchClient | None = None
    ) -> None:
        super().__init__(url, secret)
        self.database_url = database_url.replace("postgresql+psycopg://", "postgresql://", 1)
        self.local_client = local_client

    def _application_request(self, path: str, args: dict[str, Any], *, mutation: bool) -> Any:
        if self.local_client is not None:
            return self.local_client.request_sync(path, args, mutation=mutation)
        return super().request_sync(path, args, mutation=mutation)

    def request_sync(self, path: str, args: dict[str, Any], *, mutation: bool = False) -> Any:
        if path == "runtimeAutomation:saveSnapshot":
            snapshot_id = str(uuid4())
            with psycopg.connect(self.database_url) as connection:
                connection.execute(
                    """insert into public.analysis_reports (id,snapshot_id,market_json,account_json)
                       values (%s,%s,%s,%s)
                       on conflict (id) do update set snapshot_id=excluded.snapshot_id,
                         market_json=excluded.market_json, account_json=excluded.account_json, updated_at=now()""",
                    (
                        args["runId"],
                        snapshot_id,
                        Jsonb(json.loads(args["marketJson"])),
                        Jsonb(json.loads(args["accountJson"])),
                    ),
                )
            return self._application_request(
                path,
                {
                    "userId": args["userId"],
                    "runId": args["runId"],
                    "snapshotId": snapshot_id,
                },
                mutation=True,
            )
        result = self._application_request(path, args, mutation=mutation)
        if path == "runtimeAutomation:context":
            run, parent = result["run"], result.get("parent")
            with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
                connection.execute("SET TRANSACTION READ ONLY")
                snapshot = connection.execute(
                    "select snapshot_id::text as id,market_json,account_json from public.analysis_reports where id=%s",
                    (run["id"],),
                ).fetchone()
                result["snapshot"] = snapshot
                if parent:
                    report = connection.execute(
                        "select report_markdown from public.analysis_reports where id=%s",
                        (parent["id"],),
                    ).fetchone()
                    result["parent"] = {**parent, **(report or {})}
        return result

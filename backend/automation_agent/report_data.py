"""Keep research evidence in ``ai.analysis_reports``; scheduling records go through the trading writer."""

import json
from collections.abc import Callable
from typing import Any
from uuid import uuid4

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from .local_client import LocalResearchClient


class ResearchData:
    def __init__(self, client: LocalResearchClient, database_url: Callable[[], str]) -> None:
        self.client = client
        self._database_url = database_url

    @property
    def database_url(self) -> str:
        return self._database_url().replace("postgresql+psycopg://", "postgresql://", 1)

    def request_sync(self, path: str, args: dict[str, Any], *, mutation: bool = False) -> Any:
        if path == "runtimeAutomation:saveSnapshot":
            snapshot_id = str(uuid4())
            with psycopg.connect(self.database_url) as connection:
                connection.execute(
                    """insert into ai.analysis_reports (id,snapshot_id,market_json,account_json)
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
            return self.client.request_sync(
                path,
                {"userId": args["userId"], "runId": args["runId"], "snapshotId": snapshot_id},
                mutation=True,
            )
        result = self.client.request_sync(path, args, mutation=mutation)
        if path == "runtimeAutomation:context":
            run, parent = result["run"], result.get("parent")
            with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
                connection.execute("SET TRANSACTION READ ONLY")
                snapshot = connection.execute(
                    "select snapshot_id::text as id,market_json,account_json from ai.analysis_reports where id=%s",
                    (run["id"],),
                ).fetchone()
                result["snapshot"] = snapshot
                if parent:
                    report = connection.execute(
                        "select report_markdown from ai.analysis_reports where id=%s",
                        (parent["id"],),
                    ).fetchone()
                    result["parent"] = {**parent, **(report or {})}
        return result

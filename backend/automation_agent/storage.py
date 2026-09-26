"""Persist rendered analysis charts in ``ai.chart_images`` on the local PostgreSQL server."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

import psycopg

from news_agent.config import NewsAgentSettings


@dataclass(frozen=True, slots=True)
class ChartArtifact:
    id: str
    label: str
    alt_text: str
    content: bytes
    context: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class StoredChart:
    id: str
    label: str
    alt_text: str
    run_id: str
    content: bytes

    def stored_metadata(self) -> dict[str, str]:
        return {"id": self.id, "label": self.label, "altText": self.alt_text, "runId": self.run_id}


class ChartStorage:
    def __init__(self, settings: NewsAgentSettings) -> None:
        self.database_url = settings.psycopg_url()

    def save_run_charts(self, *, user_id: str, agent_run_id: str, charts: list[ChartArtifact]) -> list[StoredChart]:
        run_id = str(UUID(agent_run_id))
        if not charts:
            return []
        with psycopg.connect(self.database_url) as connection, connection.cursor() as cursor:
            cursor.executemany(
                """insert into ai.chart_images (run_id, chart_id, owner_id, content)
                   values (%s, %s, %s, %s)
                   on conflict (run_id, chart_id) do update
                     set content = excluded.content, owner_id = excluded.owner_id, created_at = now()""",
                [(run_id, chart.id, user_id, chart.content) for chart in charts],
            )
        return [
            StoredChart(id=chart.id, label=chart.label, alt_text=chart.alt_text, run_id=run_id, content=chart.content)
            for chart in charts
        ]

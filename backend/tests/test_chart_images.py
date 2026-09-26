"""Charts are stored by the analysis service and served by any API replica through signed links."""

import os
import time
from types import SimpleNamespace
from uuid import uuid4

import pytest
from psycopg_pool import AsyncConnectionPool

from app.chart_images import ChartImages, chart_link
from app.errors import AppError
from automation_agent.storage import ChartArtifact, ChartStorage

SECRET = "test-analysis-service-secret"


def test_signed_links_are_stable_within_a_window_and_reject_tampering():
    charts = ChartImages(pool=None, secret=SECRET, link_seconds=3600)  # type: ignore[arg-type]
    run_id = str(uuid4())
    now = time.time()
    first = charts.signed_path(run_id, "btc-1-minute", now=now)
    assert first == charts.signed_path(run_id, "btc-1-minute", now=now + 1)
    query = dict(item.split("=") for item in first.split("?", 1)[1].split("&"))
    charts.verify(run_id, "btc-1-minute", int(query["expires"]), query["signature"])
    for run, chart, expires, signature in (
        (run_id, "btc-daily", int(query["expires"]), query["signature"]),
        (str(uuid4()), "btc-1-minute", int(query["expires"]), query["signature"]),
        (run_id, "btc-1-minute", int(query["expires"]) + 1, query["signature"]),
        (run_id, "btc-1-minute", int(now) - 1, charts._signature(run_id, "btc-1-minute", int(now) - 1)),
        ("not-a-run", "btc-1-minute", int(query["expires"]), query["signature"]),
    ):
        with pytest.raises(AppError) as rejected:
            charts.verify(run, chart, expires, signature)
        assert rejected.value.status == 404


def test_chart_links_skip_unsafe_identifiers():
    charts = ChartImages(pool=None, secret=SECRET, link_seconds=3600)  # type: ignore[arg-type]
    run_id = str(uuid4())
    assert chart_link(charts, run_id, {"id": "../secret"}) is None
    link = chart_link(charts, run_id, {"id": "btc-daily", "label": "BTC daily"})
    assert link and link["altText"] == "BTC daily" and link["url"].startswith(f"/api/charts/{run_id}/btc-daily?")


@pytest.mark.skipif(not os.getenv("TEST_LOCAL_DATABASE_URL"), reason="No isolated local PostgreSQL test URL")
async def test_stored_chart_round_trip_and_retention():
    url = os.environ["TEST_LOCAL_DATABASE_URL"]
    run_id, owner = str(uuid4()), str(uuid4())
    stored = ChartStorage(SimpleNamespace(psycopg_url=lambda: url)).save_run_charts(  # type: ignore[arg-type]
        user_id=owner,
        agent_run_id=run_id,
        charts=[ChartArtifact(id="btc-daily", label="BTC daily", alt_text="Daily chart", content=b"\x89PNG-test")],
    )
    assert stored[0].stored_metadata() == {"id": "btc-daily", "label": "BTC daily", "altText": "Daily chart",
                                           "runId": run_id}
    async with AsyncConnectionPool(url, open=False) as pool:
        await pool.open()
        charts = ChartImages(pool, SECRET, 3600)
        assert await charts.available([run_id, "not-a-uuid"]) == {(run_id, "btc-daily")}
        assert await charts.content(run_id, "btc-daily") == b"\x89PNG-test"
        async with pool.connection() as connection:
            await connection.execute(
                "update ai.chart_images set created_at = now() - interval '91 days' where run_id=%s", (run_id,)
            )
        assert await charts.delete_expired(90) >= 1
        with pytest.raises(AppError):
            await charts.content(run_id, "btc-daily")

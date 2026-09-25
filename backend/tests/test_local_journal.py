"""Exercise the local order journal against an isolated PostgreSQL database."""

import asyncio
import os
from uuid import uuid4

import pytest
from psycopg_pool import AsyncConnectionPool

from app.errors import AppError
from app.local_journal import LocalOrderJournal

pytestmark = pytest.mark.skipif(
    not os.getenv("TEST_LOCAL_DATABASE_URL"), reason="No isolated local PostgreSQL test URL"
)


@pytest.fixture(scope="module")
def event_loop_policy():
    if os.name == "nt":
        return asyncio.WindowsSelectorEventLoopPolicy()
    return asyncio.DefaultEventLoopPolicy()


@pytest.fixture
async def journal():
    async with AsyncConnectionPool(os.environ["TEST_LOCAL_DATABASE_URL"], open=False) as pool:
        await pool.open()
        yield LocalOrderJournal(pool, f"test:{uuid4()}")


@pytest.mark.asyncio
async def test_order_identity_commits_once_and_unknown_is_not_redispatched(journal: LocalOrderJournal):
    args = {
        "clientOrderId": "order_123",
        "payload": '{"client_order_id":"order_123"}',
        "context": {"strategyId": str(uuid4())},
    }
    assert await journal.call("orderIntents:begin", args) == {"dispatch": True, "outcome": {"kind": "unknown"}}
    assert await journal.call("orderIntents:begin", args) == {"dispatch": False, "outcome": {"kind": "unknown"}}
    with pytest.raises(AppError):
        await journal.call("orderIntents:begin", {**args, "payload": "{}"})
    outcome = {"kind": "accepted", "response": '{"result":{"id":1}}'}
    await journal.call("orderIntents:resolve", {"clientOrderId": "order_123", "outcome": outcome})
    await journal.call("orderIntents:resolve", {"clientOrderId": "order_123", "outcome": outcome})
    with pytest.raises(AppError):
        await journal.call(
            "orderIntents:resolve",
            {
                "clientOrderId": "order_123",
                "outcome": {"kind": "rejected", "code": "late", "message": "late"},
            },
        )
    await journal.call("orderIntents:materialized", {"clientOrderId": "order_123"})
    page = await journal.call(
        "orderIntents:forStrategy",
        {
            "strategyId": args["context"]["strategyId"],
            "paginationOpts": {"numItems": 10, "cursor": None},
        },
        query=True,
    )
    assert len(page["page"]) == 1
    assert page["page"][0]["materialized"] is True
    assert page["page"][0]["outcome"] == outcome


@pytest.mark.asyncio
async def test_product_claim_batch_rolls_back_on_conflict(journal: LocalOrderJournal):
    first = str(uuid4())
    second = str(uuid4())
    await journal.call("orderIntents:claimProducts", {"strategyId": first, "productIds": ["1001", "1002"]})
    with pytest.raises(AppError):
        await journal.call("orderIntents:claimProducts", {"strategyId": second, "productIds": ["1003", "1002"]})
    async with journal.pool.connection() as connection:
        rows = await connection.execute(
            "select product_id from trade.product_claims where account_id=%s order by product_id",
            (journal.account_id,),
        )
        assert [item[0] for item in await rows.fetchall()] == ["1001", "1002"]


@pytest.mark.asyncio
async def test_fill_replay_can_add_fee_but_cannot_change_facts(journal: LocalOrderJournal):
    fill = {
        "fillId": "fill-1",
        "productId": "1001",
        "orderId": "order-1",
        "side": "buy",
        "quantity": "2",
        "price": "10.5",
        "commission": None,
        "occurredAt": "2026-09-24T12:00:00+00:00",
    }
    await journal.call("exchangeFills:ingest", {"fills": [fill]})
    await journal.call("exchangeFills:ingest", {"fills": [{**fill, "commission": "0.01"}]})
    page = await journal.call(
        "exchangeFills:forProduct",
        {
            "productId": "1001",
            "from": "2026-09-24T00:00:00+00:00",
            "paginationOpts": {"numItems": 10, "cursor": None},
        },
        query=True,
    )
    assert page["page"][0]["commission"] == "0.01"
    with pytest.raises(AppError):
        await journal.call("exchangeFills:ingest", {"fills": [{**fill, "price": "11"}]})

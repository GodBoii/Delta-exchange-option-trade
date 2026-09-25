"""Check that remote mirror acknowledgement follows a successful batch response."""

import asyncio
import os
from uuid import uuid4

import httpx
import pytest
from psycopg_pool import AsyncConnectionPool

from app.errors import AppError
from app.local_runtime import LocalRuntimeStore
from app.recovery_mirror import RecoveryMirror

pytestmark = pytest.mark.skipif(
    not os.getenv("TEST_LOCAL_DATABASE_URL"), reason="No isolated local PostgreSQL test URL"
)


@pytest.fixture(scope="module")
def event_loop_policy():
    if os.name == "nt":
        return asyncio.WindowsSelectorEventLoopPolicy()
    return asyncio.DefaultEventLoopPolicy()


@pytest.mark.asyncio
async def test_failed_remote_batch_remains_pending_and_success_acknowledges():
    user_id, strategy_id = str(uuid4()), str(uuid4())
    async with AsyncConnectionPool(os.environ["TEST_LOCAL_DATABASE_URL"], open=False) as pool:
        await pool.open()
        runtime = LocalRuntimeStore(pool)
        await runtime.write("strategies", {
            "id": strategy_id, "user_id": user_id, "name": "Mirror test", "status": "scheduled",
        })
        async with pool.connection() as connection:
            await connection.execute(
                "update trade.recovery_outbox set delivered_at=now() where entity_key<>%s",
                (strategy_id,),
            )
        calls = []

        def fail(request: httpx.Request) -> httpx.Response:
            calls.append(request)
            return httpx.Response(503, json={"status": "error"})

        async with httpx.AsyncClient(transport=httpx.MockTransport(fail)) as client:
            mirror = RecoveryMirror(pool, client, "https://recovery.example", "test-secret")
            with pytest.raises(AppError):
                await mirror.deliver_once()
        async with pool.connection() as connection:
            result = await connection.execute(
                "select delivered_at from trade.recovery_outbox where entity_key=%s order by id desc limit 1",
                (strategy_id,),
            )
            assert (await result.fetchone())[0] is None

        def succeed(request: httpx.Request) -> httpx.Response:
            calls.append(request)
            return httpx.Response(200, json={"status": "success", "value": 1})

        async with httpx.AsyncClient(transport=httpx.MockTransport(succeed)) as client:
            mirror = RecoveryMirror(pool, client, "https://recovery.example", "test-secret")
            assert await mirror.deliver_once() is True
            assert (await mirror.status())["pending"] == 0
        assert len(calls) == 2

"""Durable retry admission. An exhausted attempt never implies flat exposure."""

from typing import Any

from psycopg_pool import AsyncConnectionPool


class Recovery:
    def __init__(self, pool: AsyncConnectionPool, maximum: int = 5) -> None:
        self.pool, self.maximum = pool, maximum

    async def claim(self, strategy_id: str, operation: str) -> bool:
        async with self.pool.connection() as connection:
            result = await connection.execute(
                """insert into trade.recovery_attempts(strategy_id,operation,attempts,next_attempt_at)
                   values (%s,%s,1,now()+interval '5 seconds')
                   on conflict(strategy_id,operation) do update set
                     attempts=trade.recovery_attempts.attempts+1,
                     next_attempt_at=now()+least(300,5*power(2,trade.recovery_attempts.attempts)) * interval '1 second',
                     updated_at=now()
                   where not trade.recovery_attempts.exhausted
                     and trade.recovery_attempts.attempts<%s and trade.recovery_attempts.next_attempt_at<=now()
                   returning attempts""", (strategy_id, operation, self.maximum),
            )
            return await result.fetchone() is not None

    async def failed(self, strategy_id: str, operation: str, error: Exception | str) -> bool:
        code = error if isinstance(error, str) else getattr(error, "code", type(error).__name__)
        async with self.pool.connection() as connection:
            result = await connection.execute(
                """update trade.recovery_attempts set last_error_code=%s,exhausted=attempts>=%s,updated_at=now()
                   where strategy_id=%s and operation=%s returning exhausted""",
                (code, self.maximum, strategy_id, operation),
            )
            row = await result.fetchone()
            return bool(row and row[0])

    async def clear(self, strategy_id: str, operation: str | None = None) -> None:
        async with self.pool.connection() as connection:
            await connection.execute(
                "delete from trade.recovery_attempts where strategy_id=%s and (%s::text is null or operation=%s)",
                (strategy_id, operation, operation),
            )

    async def required_ids(self, strategy_ids: list[str]) -> set[str]:
        if not strategy_ids:
            return set()
        async with self.pool.connection() as connection:
            result = await connection.execute(
                """select distinct a.strategy_id from trade.recovery_attempts a
                   join trade.strategies s on s.id=a.strategy_id
                   where a.strategy_id=any(%s) and (a.exhausted or a.attempts>=%s)
                     and (s.status in ('scheduled','attention','active','executing_entry','executing_exit')
                          or a.operation='release')""",
                (strategy_ids, self.maximum),
            )
            return {row[0] for row in await result.fetchall()}

    async def status(self) -> dict[str, Any]:
        async with self.pool.connection() as connection:
            result = await connection.execute(
                """select count(*) filter(where a.exhausted or a.attempts>=%s),count(*)
                   from trade.recovery_attempts a join trade.strategies s on s.id=a.strategy_id
                   where s.status in ('scheduled','attention','active','executing_entry','executing_exit')
                      or a.operation='release'""",
                (self.maximum,),
            )
            exhausted, pending = await result.fetchone()
            return {"manualActionRequired": exhausted, "tracked": pending, "maximumAttempts": self.maximum}

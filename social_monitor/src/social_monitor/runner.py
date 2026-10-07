import asyncio
from collections.abc import Callable
from datetime import UTC, datetime

from .collector import Collector
from .config import Config, Target
from .domain import make_alert
from .store import Store


class Runner:
    def __init__(
        self,
        config: Config,
        store: Store,
        collector: Collector,
        emit: Callable[[dict], None],
        diagnostic: Callable[[dict], None],
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ):
        self.config = config
        self.store = store
        self.collector = collector
        self.emit = emit
        self.diagnostic = diagnostic
        self.clock = clock

    async def poll(self, target: Target) -> bool:
        started = self.clock()
        try:
            async with asyncio.timeout(self.config.timeout_seconds):
                result = await self.collector.fetch(target, self.config.batch_size)
        except Exception as error:
            # This is the plugin/network boundary. Retain the failure class, never credentials,
            # request headers or the upstream exception message. Cancellation still propagates.
            completed = self.clock()
            code = type(error).__name__
            self.store.complete_poll(target.key, started, completed, [], {}, "failed", code)
            self.diagnostic({"event": "poll_failed", "target": target.name, "error_code": code})
            return False
        completed = self.clock()
        alerts = {}
        should_alert = self.config.alert_on_first_poll or self.store.has_baseline(target.key)
        if should_alert:
            for post in result.posts:
                if alert := make_alert(post, completed, self.config.alert_max_age_seconds):
                    alerts[post.id] = {**alert, "target": target.name}
        inserted = self.store.complete_poll(
            target.key,
            started,
            completed,
            result.posts,
            alerts,
            result.status,
            result.error_code,
        )
        self.diagnostic(
            {
                "event": "poll_completed",
                "target": target.name,
                "status": result.status,
                "fetched": len(result.posts),
                "inserted": inserted,
                "baseline": not should_alert,
                "batch_limit_reached": len(result.posts) >= self.config.batch_size,
                "error_code": result.error_code,
            }
        )
        self.store.deliver(self.emit, completed)
        return result.status in {"ok", "empty"}

    async def run(self, once: bool = False) -> bool:
        if not self.config.targets:
            raise ValueError("run requires at least one configured target")
        self.store.deliver(self.emit, self.clock())
        failures = dict.fromkeys((target.key for target in self.config.targets), 0)
        due = dict.fromkeys(failures, 0.0)
        loop = asyncio.get_running_loop()
        while True:
            healthy = True
            for target in self.config.targets:
                if not once and loop.time() < due[target.key]:
                    continue
                ok = await self.poll(target)
                healthy = healthy and ok
                failures[target.key] = 0 if ok else min(failures[target.key] + 1, 6)
                delay = min(self.config.poll_seconds * 2 ** failures[target.key], 3600)
                due[target.key] = loop.time() + delay
            if once:
                return healthy
            await asyncio.sleep(max(0.1, min(due.values()) - loop.time()))

import asyncio
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

from .collector import Collector
from .config import Config, Target
from .diagnostics import ACCESS_ERRORS
from .domain import make_alert
from .relevance import VERSION, classify
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
        *,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ):
        self.config, self.store, self.collector = config, store, collector
        self.emit, self.diagnostic, self.clock = emit, diagnostic, clock
        self.monotonic, self.sleep = monotonic, sleep
        self.retry_after = 0.0
        self.stop_reason: str | None = None
        self.useful_partial = False
        self.last_error: str | None = None

    async def poll(self, target: Target) -> bool:
        started, tick = self.clock(), self.monotonic()
        self.retry_after = 0
        self.useful_partial = False
        self.last_error = None
        reconcile = self.store.reconciliation_due(target.key, started, self.config.reconcile_seconds)
        known = self.store.recent_ids(target.key, target.value if target.kind == "user" else None)
        try:
            async with asyncio.timeout(self.config.timeout_seconds):
                result = await self.collector.fetch(
                    target, self.config.batch_size, known_ids=known, reconcile=reconcile
                )
        except Exception as error:
            completed, code = self.clock(), type(error).__name__
            self.last_error = code
            self.store.complete_poll(
                target.key,
                started,
                completed,
                [],
                {},
                "failed",
                code,
                details={"duration_seconds": self.monotonic() - tick},
            )
            self.diagnostic({"event": "poll_failed", "target": target.name, "error_code": code})
            return False
        completed = self.clock()
        alerts = {}
        assessments = {}
        should_alert = self.config.alert_on_first_poll or self.store.has_baseline(target.key)
        existing = self.store.existing_ids([post.id for post in result.posts])
        for post in result.posts:
            if post.id in existing:
                continue
            assessment = classify(post)
            alert = make_alert(post, completed, self.config.alert_max_age_seconds)
            assessment["study_eligible"] = should_alert and alert is not None
            assessment["target"] = target.name
            assessments[post.id] = assessment
            if (
                should_alert
                and alert is not None
                and (not self.config.crypto_only or assessment["relevance"] == "crypto")
            ):
                alerts[post.id] = {**alert, "target": target.name, "collector_version": 4, "crypto": assessment}
        details = {
            **result.details,
            "screening_version": VERSION,
            "assessment_scope": "new_posts",
            "duration_seconds": round(self.monotonic() - tick, 4),
            "interval_seconds": self.config.interval(target),
            "retry_after_seconds": result.retry_after_seconds,
            "relevance_counts": {
                label: sum(item["relevance"] == label for item in assessments.values())
                for label in ("crypto", "uncertain", "unrelated")
            },
        }
        inserted = self.store.complete_poll(
            target.key,
            started,
            completed,
            result.posts,
            alerts,
            result.status,
            result.error_code,
            details=details,
            reconciled=reconcile,
            assessments=assessments,
        )
        self.diagnostic(
            {
                "event": "poll_completed",
                "target": target.name,
                "status": result.status,
                "fetched": len(result.posts),
                "inserted": inserted,
                "baseline": not should_alert,
                "error_code": result.error_code,
                **details,
            }
        )
        self.store.deliver(self.emit, completed)
        self.retry_after = result.retry_after_seconds
        self.last_error = result.error_code
        self.useful_partial = bool(
            result.posts
            and result.error_code == "pagination_stalled"
            and result.details.get("known_overlap")
            and set(result.details.get("warnings", []))
            <= {"pagination_stalled", "unsupported_card", "unsupported_media"}
        )
        if result.error_code in ACCESS_ERRORS:
            self.stop_reason = result.error_code
            self.diagnostic(
                {
                    "event": "collection_stopped",
                    "error_code": self.stop_reason,
                    "action_required": "Check the dedicated X session before restarting",
                }
            )
        return result.status in {"ok", "empty"}

    async def run(self, once: bool = False) -> bool:
        if not self.config.targets:
            raise ValueError("run requires at least one configured target")
        self.store.deliver(self.emit, self.clock())
        if once:
            healthy = True
            for target in self.config.targets:
                healthy = await self.poll(target) and healthy
                if self.stop_reason:
                    return False
            return healthy
        failures = dict.fromkeys((target.key for target in self.config.targets), 0)
        due = dict.fromkeys(failures, self.monotonic())
        last_start = float("-inf")
        while True:
            target = min(self.config.targets, key=lambda item: due[item.key])
            ready = max(due[target.key], last_start + self.config.request_spacing_seconds)
            if ready > self.monotonic():
                await self.sleep(ready - self.monotonic())
            last_start = self.monotonic()
            ok = await self.poll(target)
            if self.stop_reason:
                return False
            failures[target.key] = 0 if ok or self.useful_partial else min(failures[target.key] + 1, 6)
            delay = min(self.config.interval(target) * 2 ** failures[target.key], 3600)
            # Two short, spaced recovery checks before sustained failures back off normally.
            if (
                self.last_error in {"empty_unverified", "TimeoutError", "upstream_busy", "transport_error"}
                and failures[target.key] <= 2
            ):
                delay = min(delay, 30 * 2 ** (failures[target.key] - 1))
            delay = max(delay, self.retry_after)
            due[target.key] = max(last_start + delay, self.monotonic() + self.config.request_spacing_seconds)

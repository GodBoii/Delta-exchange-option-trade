from __future__ import annotations

import time
from collections import Counter


class ResearchBudget:
    """One budget per analysis, shared by search and article tools, including nested reads."""

    def __init__(
        self,
        seconds: float = 240,
        calls_per_tool: int = 10,
        limits: dict[str, int] | None = None,
        verification_reserve_seconds: float = 45,
    ) -> None:
        self.seconds = seconds
        self.calls_per_tool = calls_per_tool
        self.limits = limits or {}
        self.deadline: float | None = None
        self.calls: Counter[str] = Counter()
        self.verification_reserve_seconds = max(0, min(verification_reserve_seconds, seconds / 2))

    def snapshot(self) -> dict:
        """Describe available resources without starting the research clock."""
        remaining = self.seconds if self.deadline is None else max(0, self.deadline - time.monotonic())
        names = set(self.limits) | set(self.calls)
        return {
            "remaining_seconds": round(remaining, 1),
            "remaining_calls": {
                name: max(0, self.limits.get(name, self.calls_per_tool) - self.calls[name])
                for name in sorted(names)
            },
            "verification_reserve_seconds": self.verification_reserve_seconds,
        }

    def remaining(self, maximum: float) -> float:
        if self.deadline is None:
            self.deadline = time.monotonic() + self.seconds
        remaining = min(maximum, self.deadline - time.monotonic())
        if remaining <= 0:
            raise TimeoutError("News research deadline reached; synthesize the available evidence")
        return remaining

    def consume(self, tool: str) -> None:
        remaining = self.remaining(self.seconds)
        if tool in {"web_search", "search_news", "search_public_discussion", "search_exchange_announcements",
                    "curate_public_sources"} and remaining <= self.verification_reserve_seconds:
            raise ValueError("Discovery budget finished; read collected originals and synthesize available evidence")
        limit = self.limits.get(tool, self.calls_per_tool)
        if self.calls[tool] >= limit:
            raise ValueError(f"{tool} reached its {limit}-call limit for this analysis")
        self.calls[tool] += 1

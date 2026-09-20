from __future__ import annotations

import time
from collections import Counter


class ResearchBudget:
    """One budget per analysis, shared by search and article tools, including nested reads."""

    def __init__(self, seconds: float = 90, calls_per_tool: int = 10) -> None:
        self.seconds = seconds
        self.calls_per_tool = calls_per_tool
        self.deadline: float | None = None
        self.calls: Counter[str] = Counter()

    def remaining(self, maximum: float) -> float:
        if self.deadline is None:
            self.deadline = time.monotonic() + self.seconds
        remaining = min(maximum, self.deadline - time.monotonic())
        if remaining <= 0:
            raise TimeoutError("News research deadline reached; synthesize the available evidence")
        return remaining

    def consume(self, tool: str) -> None:
        self.remaining(self.seconds)
        if self.calls[tool] >= self.calls_per_tool:
            raise ValueError(f"{tool} reached its {self.calls_per_tool}-call limit for this analysis")
        self.calls[tool] += 1

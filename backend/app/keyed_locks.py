"""Per-key coordination whose bookkeeping disappears when the last waiter leaves."""

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass, field


@dataclass
class _Entry:
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    users: int = 0


class KeyedLocks:
    def __init__(self) -> None:
        self.entries: dict[str, _Entry] = {}

    @asynccontextmanager
    async def hold(self, key: str):
        entry = self.entries.setdefault(key, _Entry())
        entry.users += 1
        try:
            async with entry.lock:
                yield
        finally:
            entry.users -= 1
            if not entry.users:
                self.entries.pop(key, None)

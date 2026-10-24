"""Share bounded source snapshots between concurrent BTC and ETH research loops."""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import logging
import math
import os
import threading
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from concurrent.futures import Future
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any


@dataclass(frozen=True)
class SourceSnapshot:
    cards: list[dict[str, Any]]
    checked_at: str
    error: str | None = None
    error_code: str | None = None
    retry_seconds: float = 60
    last_success_at: str | None = None


class SourceCache:
    """Single-flight cache using thread-safe futures, never cross-loop asyncio locks."""

    def __init__(self, capacity: int = 64, clock: Callable[[], float] = time.monotonic) -> None:
        self.capacity = capacity
        self.clock = clock
        self.lock = threading.Lock()
        self.entries: OrderedDict[tuple, tuple[float, SourceSnapshot]] = OrderedDict()
        self.pending: dict[tuple, Future] = {}

    async def get(self, key: tuple, load: Callable[[], Awaitable[SourceSnapshot]]) -> SourceSnapshot:
        with self.lock:
            entry = self.entries.get(key)
            if entry and entry[0] > self.clock():
                self.entries.move_to_end(key)
                return copy.deepcopy(entry[1])
            owner = key not in self.pending
            future = self.pending.setdefault(key, Future())
        if not owner:
            return copy.deepcopy(await asyncio.shield(asyncio.wrap_future(future)))
        try:
            snapshot = await load()
            with self.lock:
                previous = self.entries.get(key)
                snapshot = replace(
                    snapshot,
                    last_success_at=(
                        snapshot.checked_at
                        if not snapshot.error
                        else snapshot.last_success_at or (previous[1].last_success_at if previous else None)
                    ),
                )
                self.entries[key] = (self.clock() + snapshot.retry_seconds, snapshot)
                self.entries.move_to_end(key)
                while len(self.entries) > self.capacity:
                    self.entries.popitem(last=False)
            future.set_result(snapshot)
            return copy.deepcopy(snapshot)
        except BaseException as error:
            # Wake other event loops on cancellation as well as ordinary failures.
            future.set_exception(error)
            raise
        finally:
            with self.lock:
                self.pending.pop(key, None)


class FileSourceCache(SourceCache):
    """Persist public snapshots under an OS lock shared by isolated analysis workers."""

    def __init__(self, directory: Path) -> None:
        super().__init__()
        self.directory = directory
        directory.mkdir(parents=True, exist_ok=True)

    async def get(self, key: tuple, load: Callable[[], Awaitable[SourceSnapshot]]) -> SourceSnapshot:
        async def shared_load() -> SourceSnapshot:
            digest = hashlib.sha256(json.dumps(key, sort_keys=True).encode()).hexdigest()
            path = self.directory / f"{digest}.json"
            with (self.directory / f"{digest}.lock").open("a+b") as lock_file:
                while not _try_lock(lock_file):
                    await asyncio.sleep(0.1)
                try:
                    previous = None
                    if path.exists():
                        try:
                            if path.stat().st_size > 2_000_000:
                                raise ValueError("oversized source snapshot")
                            saved = json.loads(path.read_text(encoding="utf-8"))
                            if not isinstance(saved, dict) or not isinstance(saved.get("snapshot"), dict):
                                raise ValueError("invalid source snapshot")
                            previous = SourceSnapshot(**saved["snapshot"])
                            if not isinstance(previous.cards, list) or not all(
                                isinstance(row, dict) for row in previous.cards
                            ):
                                raise ValueError("source cards must be a list of objects")
                            if type(saved.get("expires_at")) not in (int, float) or not math.isfinite(
                                saved["expires_at"]
                            ):
                                raise ValueError("invalid source expiry")
                            remaining = saved["expires_at"] - time.time()
                            if remaining > 0:
                                return replace(previous, retry_seconds=remaining)
                        except (ValueError, KeyError, TypeError):
                            logging.getLogger(__name__).warning(
                                "Discarding invalid public-source cache file %s", digest
                            )
                    snapshot = await load()
                    snapshot = replace(
                        snapshot,
                        last_success_at=(
                            snapshot.checked_at
                            if not snapshot.error
                            else previous.last_success_at
                            if previous
                            else None
                        ),
                    )
                    with NamedTemporaryFile(mode="w", encoding="utf-8", dir=self.directory, delete=False) as temporary:
                        json.dump(
                            {"expires_at": time.time() + snapshot.retry_seconds, "snapshot": asdict(snapshot)},
                            temporary,
                        )
                        temporary_path = Path(temporary.name)
                    try:
                        os.replace(temporary_path, path)
                    finally:
                        temporary_path.unlink(missing_ok=True)
                    return snapshot
                finally:
                    _unlock(lock_file)

        return await super().get(key, shared_load)


def _try_lock(stream) -> bool:
    if os.name == "nt":
        import msvcrt

        stream.seek(0)
        try:
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            return True
        except OSError:
            return False
    import fcntl

    try:
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except BlockingIOError:
        return False


def _unlock(stream) -> None:
    if os.name == "nt":
        import msvcrt

        stream.seek(0)
        msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        import fcntl

        fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


SOURCE_CACHE = (
    FileSourceCache(Path(os.environ["NEWS_STATE_DIR"]) / "sources") if os.getenv("NEWS_STATE_DIR") else SourceCache()
)

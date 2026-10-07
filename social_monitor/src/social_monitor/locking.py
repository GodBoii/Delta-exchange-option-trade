"""One writer per state directory; OS releases the lock after process exit."""

import os
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def writer_lock(state_dir: Path):
    state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = state_dir / "writer.lock"
    with path.open("a+b") as handle:
        if path.stat().st_size == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        if os.name == "nt":
            import msvcrt

            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as error:
                raise ValueError("another monitor writer is using this state directory") from error
            try:
                yield
            finally:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as error:
                raise ValueError("another monitor writer is using this state directory") from error
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

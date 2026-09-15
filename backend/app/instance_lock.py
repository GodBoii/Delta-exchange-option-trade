"""Prevent two backend containers sharing the state volume from issuing trades."""

import os
from pathlib import Path
from typing import BinaryIO


class InstanceLock:
    def __init__(self, path: str) -> None:
        self.path = Path(path)
        self.file: BinaryIO | None = None

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.path.open("a+b")
        try:
            if os.name == "nt":
                import msvcrt

                handle.seek(0)
                if not handle.read(1):
                    handle.write(b"0")
                    handle.flush()
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            handle.close()
            raise RuntimeError("Another trading backend owns the application state volume") from error
        self.file = handle

    def close(self) -> None:
        if self.file is not None:
            self.file.close()
            self.file = None

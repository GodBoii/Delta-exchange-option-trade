"""Run synchronous model/SDK work in a process that can be stopped at its deadline."""

from __future__ import annotations

import logging
import multiprocessing
import time
from collections.abc import Callable
from multiprocessing.connection import Connection
from typing import Any

logger = logging.getLogger(__name__)


def _execute(connection: Connection, function: Callable, args: tuple) -> None:
    try:
        connection.send((True, function(*args)))
    except Exception as exc:
        logger.exception("Analysis worker failed")
        connection.send((False, str(exc) or type(exc).__name__))
    finally:
        connection.close()


def run_in_worker(function: Callable, *args: Any, timeout_seconds: float) -> Any:
    """Bound the entire job, including blocking DNS, SDK retries, parsing, and database I/O."""
    context = multiprocessing.get_context("spawn")
    receiver, sender = context.Pipe(duplex=False)
    process = context.Process(target=_execute, args=(sender, function, args))
    deadline = time.monotonic() + timeout_seconds
    try:
        process.start()
        sender.close()
        if not receiver.poll(max(0, deadline - time.monotonic())):
            raise TimeoutError(f"Analysis exceeded its {timeout_seconds:g}-second deadline")
        try:
            success, result = receiver.recv()
        except EOFError as exc:
            raise RuntimeError("Analysis worker exited without a result") from exc
        if not success:
            raise RuntimeError(result)
        return result
    finally:
        receiver.close()
        sender.close()
        if process.pid is not None:
            if process.is_alive():
                process.terminate()
            process.join(timeout=1)
            if process.is_alive():
                process.kill()
                process.join(timeout=1)
            process.close()

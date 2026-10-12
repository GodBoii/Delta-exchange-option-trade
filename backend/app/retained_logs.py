"""Retain backend failure evidence across container replacements."""

import logging
import os
import time
from contextlib import suppress
from logging.handlers import RotatingFileHandler
from pathlib import Path


class RedactedFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        message = super().format(record)
        for key in ("LOCAL_DATABASE_URL", "LOCAL_READER_DATABASE_URL", "AI_DATABASE_URL",
                    "ANALYSIS_SERVICE_SECRET", "CREDENTIAL_ENCRYPTION_KEY", "SUPABASE_SERVICE_ROLE_KEY",
                    "OPENROUTER_API_KEY"):
            value = os.getenv(key)
            if value:
                message = message.replace(value, "[redacted]")
        return message


def configure_retained_logs() -> None:
    directory_name = os.getenv("TRADING_LOG_DIR")
    if not directory_name:
        return
    root = logging.getLogger()
    if any(getattr(handler, "trading_retained", False) for handler in root.handlers):
        return
    directory = Path(directory_name)
    directory.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(
        directory / f"backend-{time.time_ns()}-{os.getpid()}.log",
        maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8",
    )
    handler.trading_retained = True
    handler.setLevel(logging.INFO)
    handler.setFormatter(RedactedFormatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    root.addHandler(handler)
    for old in directory.glob("backend-*.log*"):
        with suppress(FileNotFoundError):
            if old.stat().st_mtime < time.time() - 30 * 24 * 3600:
                old.unlink(missing_ok=True)

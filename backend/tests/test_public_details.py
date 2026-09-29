"""Internal details (model, infrastructure state, raw provider errors) stay off public responses."""

from types import SimpleNamespace

import pytest

from app import main
from app.automation import ANALYSIS_FAILED_MESSAGE, public_run_error

TIMEOUT_ERROR = "Automation analysis did not respond within 2700 seconds"


def probe(host: str, headers: dict[str, str] | None = None) -> SimpleNamespace:
    return SimpleNamespace(client=SimpleNamespace(host=host), headers=headers or {})


@pytest.mark.parametrize(
    ("host", "headers", "local"),
    [
        ("127.0.0.1", None, True),
        ("::1", None, True),
        # A tunnel on the same host connects from loopback but adds forwarding headers.
        ("127.0.0.1", {"cf-connecting-ip": "203.0.113.9"}, False),
        ("127.0.0.1", {"x-forwarded-for": "203.0.113.9"}, False),
        ("172.18.0.4", None, False),
        ("testclient", None, False),
    ],
)
def test_only_direct_loopback_calls_are_local_probes(host, headers, local) -> None:
    assert main.is_local_probe(probe(host, headers)) is local  # type: ignore[arg-type]


async def test_public_health_names_the_service_and_nothing_else() -> None:
    assert await main.health(probe("172.18.0.4")) == {"success": True, "service": "delta-strategy-api"}  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("stored", "shown"),
    [
        (None, None),
        ("", None),
        ("Automation was turned off", "Automation was turned off"),
        (TIMEOUT_ERROR, TIMEOUT_ERROR),
        ("OpenRouter 429: deepseek rate limited", ANALYSIS_FAILED_MESSAGE),
        ("ConnectError: news-analyzer:8002", ANALYSIS_FAILED_MESSAGE),
    ],
)
def test_run_errors_are_masked_unless_written_by_the_app(stored, shown) -> None:
    assert public_run_error(stored) == shown

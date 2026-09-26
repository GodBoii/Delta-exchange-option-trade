"""Test-only configuration. Real values come from the environment when a suite needs them."""

import os

os.environ.setdefault("NEXT_PUBLIC_SUPABASE_URL", "https://example.supabase.test")
os.environ.setdefault("NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY", "test-publishable-key")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test-service-role-key")
os.environ.setdefault(
    "LOCAL_DATABASE_URL",
    os.getenv("TEST_LOCAL_DATABASE_URL", "postgresql://trade_writer:unused@127.0.0.1:9/trade_cognition"),
)
# A fixed, test-only Fernet key; production keys never appear in the repository.
os.environ.setdefault("CREDENTIAL_ENCRYPTION_KEY", "4Wq6Qe8n1Jg0z3wQ2T5Q8b3Vt0hX9hL3s2Q1kYc7m0A=")
os.environ.setdefault("ANALYSIS_SERVICE_SECRET", "test-analysis-service-secret")

import asyncio  # noqa: E402

import pytest  # noqa: E402


@pytest.fixture(scope="session")
def event_loop_policy():
    # psycopg's async driver needs a selector loop on Windows; Linux uses the default.
    if os.name == "nt":
        return asyncio.WindowsSelectorEventLoopPolicy()
    return asyncio.DefaultEventLoopPolicy()

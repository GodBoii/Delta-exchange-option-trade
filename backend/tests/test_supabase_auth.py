"""Login tokens are verified locally against the project's published signing keys."""

import json
import time
from types import SimpleNamespace

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec

from app.database import Database
from app.supabase_auth import SupabaseAuth

URL = "https://project.supabase.test"
KID = "key-1"


def signing_key():
    private = ec.generate_private_key(ec.SECP256R1())
    public = json.loads(jwt.algorithms.ECAlgorithm.to_jwk(private.public_key()))
    return private, {**public, "kid": KID, "alg": "ES256", "use": "sig"}


def token(private, **overrides) -> str:
    now = int(time.time())
    claims = {
        "sub": "user-1", "email": "user@example.com", "aud": "authenticated", "role": "authenticated",
        "iss": f"{URL}/auth/v1", "iat": now, "exp": now + 3600,
        "user_metadata": {"full_name": "Test User"}, **overrides,
    }
    return jwt.encode(claims, private, algorithm="ES256", headers={"kid": KID})


def auth_with(handler) -> tuple[SupabaseAuth, list[str]]:
    calls: list[str] = []

    def record(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return handler(request)

    return SupabaseAuth(URL, "publishable", httpx.AsyncClient(transport=httpx.MockTransport(record))), calls


async def test_valid_token_is_verified_without_a_user_lookup():
    private, public = signing_key()
    auth, calls = auth_with(lambda _: httpx.Response(200, json={"keys": [public]}))
    user = await auth.user(token(private))
    assert user and user["id"] == "user-1" and user["email"] == "user@example.com"
    assert user["user_metadata"] == {"full_name": "Test User"}
    assert await auth.user(token(private)) == user
    assert calls == ["/auth/v1/.well-known/jwks.json"]


@pytest.mark.parametrize("overrides", [
    {"exp": int(time.time()) - 60},
    {"aud": "anon"},
    {"iss": "https://other.supabase.test/auth/v1"},
    {"role": "anon"},
])
async def test_expired_or_foreign_tokens_are_rejected(overrides):
    private, public = signing_key()
    auth, _ = auth_with(lambda _: httpx.Response(200, json={"keys": [public]}))
    assert await auth.user(token(private, **overrides)) is None


async def test_token_signed_by_another_key_is_rejected():
    private, public = signing_key()
    other, _ = signing_key()
    auth, _ = auth_with(lambda _: httpx.Response(200, json={"keys": [public]}))
    assert await auth.user(token(other)) is None
    assert await auth.user("not-a-token") is None


async def test_symmetric_tokens_fall_back_to_the_user_endpoint():
    secret = "shared-secret-for-tests-only-0123456789abcdef"
    legacy = jwt.encode({"sub": "user-2", "aud": "authenticated"}, secret, algorithm="HS256")

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/auth/v1/user"
        return httpx.Response(200, json={"id": "user-2"})

    auth, calls = auth_with(handler)
    assert await auth.user(legacy) == {"id": "user-2"}
    assert calls == ["/auth/v1/user"]


async def test_profiles_are_cached_and_stale_copies_cover_outages():
    responses = [httpx.Response(200, json=[{"user_type": "owner"}]), httpx.Response(503)]
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.params["id"])
        return responses.pop(0)

    db = Database.__new__(Database)
    db.settings = SimpleNamespace(supabase_url=URL, auth_profile_cache_seconds=60)
    db.admin_headers = {}
    db.profile_cache = __import__("collections").OrderedDict()
    db.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    assert await db.profile("user-1") == {"user_type": "owner"}
    assert await db.profile("user-1") == {"user_type": "owner"}
    assert calls == ["eq.user-1"]
    db.settings.auth_profile_cache_seconds = 0
    assert await db.profile("user-1") == {"user_type": "owner"}
    assert calls == ["eq.user-1", "eq.user-1"]
    await db.client.aclose()

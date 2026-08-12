import sys
import types
import base64
import json
import time
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import pytest
from gotrue.errors import AuthApiError

# The production image installs redis from requirements.txt. Keep this focused
# unit test runnable in lightweight developer environments that omit it.
try:
    import redis  # noqa: F401
except ModuleNotFoundError:
    redis_stub = types.ModuleType("redis")
    redis_stub.Redis = object
    redis_stub.from_url = Mock()
    sys.modules["redis"] = redis_stub

import utils


class FakeRedis:
    def __init__(self):
        self.data = {}
        self.set_calls = []

    def get(self, key):
        return self.data.get(key)

    def set(self, key, value, ex=None, nx=False):
        self.set_calls.append((key, value, ex, nx))
        if nx and key in self.data:
            return False
        self.data[key] = value
        return True

    def eval(self, _script, _key_count, key, owner):
        if self.data.get(key) == owner:
            self.data.pop(key, None)
            return 1
        return 0


def make_test_jwt(*, expires_in=3600):
    payload = json.dumps({"exp": time.time() + expires_in}).encode("utf-8")
    encoded = base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")
    return f"header.{encoded}.signature"


def test_auth_transport_failure_retries_then_returns_503(monkeypatch):
    get_user = Mock(side_effect=httpx.ConnectError("resolver unavailable"))
    monkeypatch.setattr(utils.supabase_client.auth, "get_user", get_user)
    monkeypatch.setattr(utils.time, "sleep", lambda _seconds: None)

    user, error = utils._validate_user_with_supabase("test-jwt")

    assert user is None
    assert error == ("Authentication service temporarily unavailable", 503)
    assert get_user.call_count == 2


def test_auth_transport_retry_can_recover(monkeypatch):
    expected_user = SimpleNamespace(id="user-123")
    get_user = Mock(
        side_effect=[
            httpx.ConnectError("temporary resolver failure"),
            SimpleNamespace(user=expected_user),
        ]
    )
    monkeypatch.setattr(utils.supabase_client.auth, "get_user", get_user)
    monkeypatch.setattr(utils.time, "sleep", lambda _seconds: None)

    user, error = utils._validate_user_with_supabase("test-jwt")

    assert error is None
    assert user is expected_user
    assert get_user.call_count == 2


def test_auth_api_error_returns_401_without_retry(monkeypatch):
    get_user = Mock(
        side_effect=AuthApiError("invalid token", 401, "bad_jwt")
    )
    monkeypatch.setattr(utils.supabase_client.auth, "get_user", get_user)
    monkeypatch.setattr(utils.time, "sleep", lambda _seconds: None)

    user, error = utils._validate_user_with_supabase("test-jwt")

    assert user is None
    assert error == ("Invalid or expired token", 401)
    assert get_user.call_count == 1


def test_missing_user_returns_401_without_retry(monkeypatch):
    get_user = Mock(return_value=SimpleNamespace(user=None))
    monkeypatch.setattr(utils.supabase_client.auth, "get_user", get_user)
    monkeypatch.setattr(utils.time, "sleep", lambda _seconds: None)

    user, error = utils._validate_user_with_supabase("test-jwt")

    assert user is None
    assert error == ("Invalid or expired token", 401)
    assert get_user.call_count == 1


def test_unexpected_programming_error_is_not_hidden(monkeypatch):
    get_user = Mock(side_effect=ValueError("unexpected response shape"))
    monkeypatch.setattr(utils.supabase_client.auth, "get_user", get_user)

    with pytest.raises(ValueError, match="unexpected response shape"):
        utils._validate_user_with_supabase("test-jwt")

    assert get_user.call_count == 1


def test_rest_auth_propagates_transport_outage_as_503(monkeypatch):
    fake_redis = FakeRedis()
    request = SimpleNamespace(
        headers={"Authorization": "Bearer test-jwt"}
    )
    monkeypatch.setattr(utils, "_get_jwt_redis", lambda: fake_redis)
    monkeypatch.setattr(utils, "_user_from_cache", lambda _jwt: None)
    monkeypatch.setattr(utils.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(
        utils.supabase_client.auth,
        "get_user",
        Mock(side_effect=httpx.ConnectError("resolver unavailable")),
    )

    user, error = utils.get_user_from_token(request)

    assert user is None
    assert error == ("Authentication service temporarily unavailable", 503)


def test_socket_auth_success_still_writes_through_to_cache(monkeypatch):
    fake_redis = FakeRedis()
    expected_user = SimpleNamespace(id="user-123")
    cache_write = Mock()
    monkeypatch.setattr(utils, "_get_jwt_redis", lambda: fake_redis)
    monkeypatch.setattr(utils, "_user_from_cache", lambda _jwt: None)
    monkeypatch.setattr(utils, "_user_to_cache", cache_write)
    monkeypatch.setattr(
        utils.supabase_client.auth,
        "get_user",
        Mock(return_value=SimpleNamespace(user=expected_user)),
    )

    user, error = utils.get_user_from_jwt("test-jwt")

    assert error is None
    assert user is expected_user
    cache_write.assert_called_once_with("test-jwt", expected_user)


def test_successful_validation_writes_active_and_stale_cache(monkeypatch):
    fake_redis = FakeRedis()
    monkeypatch.setattr(utils, "_get_jwt_redis", lambda: fake_redis)

    utils._user_to_cache("test-jwt", SimpleNamespace(id="user-123", email="a@example.com"))

    assert utils._jwt_cache_key("test-jwt") in fake_redis.data
    assert utils._jwt_stale_key("test-jwt") in fake_redis.data
    ttl_by_key = {key: ex for key, _value, ex, _nx in fake_redis.set_calls}
    assert ttl_by_key[utils._jwt_cache_key("test-jwt")] == utils._JWT_CACHE_TTL_SECONDS
    assert ttl_by_key[utils._jwt_stale_key("test-jwt")] == utils._JWT_STALE_TTL_SECONDS


def test_recent_outage_short_circuits_repeated_provider_calls(monkeypatch):
    fake_redis = FakeRedis()
    jwt = make_test_jwt()
    get_user = Mock(side_effect=httpx.ConnectError("resolver unavailable"))
    monkeypatch.setattr(utils, "_get_jwt_redis", lambda: fake_redis)
    monkeypatch.setattr(utils.supabase_client.auth, "get_user", get_user)
    monkeypatch.setattr(utils.time, "sleep", lambda _seconds: None)

    first_user, first_error = utils.get_user_from_jwt(jwt)
    second_user, second_error = utils.get_user_from_jwt(jwt)

    assert first_user is None
    assert second_user is None
    assert first_error == ("Authentication service temporarily unavailable", 503)
    assert second_error == first_error
    assert get_user.call_count == 2


def test_verified_stale_identity_is_used_only_during_transport_outage(monkeypatch):
    fake_redis = FakeRedis()
    jwt = make_test_jwt()
    stale_payload = json.dumps({"id": "user-123", "email": "a@example.com"})
    fake_redis.data[utils._jwt_stale_key(jwt)] = stale_payload
    get_user = Mock(side_effect=httpx.ConnectError("resolver unavailable"))
    monkeypatch.setattr(utils, "_get_jwt_redis", lambda: fake_redis)
    monkeypatch.setattr(utils.supabase_client.auth, "get_user", get_user)
    monkeypatch.setattr(utils.time, "sleep", lambda _seconds: None)

    user, error = utils.get_user_from_jwt(jwt)

    assert error is None
    assert user.id == "user-123"
    assert get_user.call_count == 2


def test_expired_jwt_never_uses_stale_identity(monkeypatch):
    fake_redis = FakeRedis()
    jwt = make_test_jwt(expires_in=-1)
    fake_redis.data[utils._jwt_stale_key(jwt)] = json.dumps({"id": "user-123"})
    fake_redis.data[utils._jwt_auth_outage_key(jwt)] = "1"
    get_user = Mock()
    monkeypatch.setattr(utils, "_get_jwt_redis", lambda: fake_redis)
    monkeypatch.setattr(utils.supabase_client.auth, "get_user", get_user)

    user, error = utils.get_user_from_jwt(jwt)

    assert user is None
    assert error == ("Authentication service temporarily unavailable", 503)
    get_user.assert_not_called()


def test_concurrent_cache_miss_waits_for_in_flight_validation(monkeypatch):
    fake_redis = FakeRedis()
    jwt = make_test_jwt()
    fake_redis.data[utils._jwt_validation_lock_key(jwt)] = "other-request"
    get_user = Mock()

    def publish_winner_result(_seconds):
        fake_redis.data[utils._jwt_cache_key(jwt)] = json.dumps({"id": "user-123"})

    monkeypatch.setattr(utils, "_get_jwt_redis", lambda: fake_redis)
    monkeypatch.setattr(utils.supabase_client.auth, "get_user", get_user)
    monkeypatch.setattr(utils.time, "sleep", publish_winner_result)

    user, error = utils.get_user_from_jwt(jwt)

    assert error is None
    assert user.id == "user-123"
    get_user.assert_not_called()

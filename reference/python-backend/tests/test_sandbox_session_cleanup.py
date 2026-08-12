import json
import sys
import types

extensions_stub = types.ModuleType("extensions")
extensions_stub.RedisClient = object
sys.modules.setdefault("extensions", extensions_stub)

from session_service import ConnectionManager


class FakeRedis:
    def __init__(self):
        self.values = {}
        self.sorted_sets = {}

    def get(self, key):
        return self.values.get(key)

    def zadd(self, key, mapping):
        self.sorted_sets.setdefault(key, {}).update(mapping)

    def zrangebyscore(self, key, minimum, maximum, start=0, num=None):
        matches = [
            member
            for member, score in self.sorted_sets.get(key, {}).items()
            if float(minimum) <= score <= float(maximum)
        ]
        matches.sort()
        return matches[start : start + num if num is not None else None]

    def zrem(self, key, member):
        self.sorted_sets.get(key, {}).pop(member, None)


def _manager():
    manager = ConnectionManager.__new__(ConnectionManager)
    manager.redis_client = FakeRedis()
    return manager


def test_expired_session_retains_sandbox_id_until_container_cleanup():
    manager = _manager()
    deleted = []
    manager._cleanup_sandbox = lambda sandbox_id: deleted.append(sandbox_id) or True

    member = manager._sandbox_expiry_member("conversation-1", "sandbox-1")
    manager.redis_client.zadd(manager.SANDBOX_EXPIRY_ZSET, {member: 0})

    assert manager.cleanup_expired_sandboxes() == 1
    assert deleted == ["sandbox-1"]
    assert member not in manager.redis_client.sorted_sets[manager.SANDBOX_EXPIRY_ZSET]


def test_live_session_reschedules_sandbox_instead_of_deleting_it():
    manager = _manager()
    deleted = []
    manager._cleanup_sandbox = lambda sandbox_id: deleted.append(sandbox_id) or True

    conversation_id = "conversation-2"
    sandbox_id = "sandbox-2"
    manager.redis_client.values[f"session:{conversation_id}"] = json.dumps(
        {"sandbox_ids": [sandbox_id]}
    )
    member = manager._sandbox_expiry_member(conversation_id, sandbox_id)
    manager.redis_client.zadd(manager.SANDBOX_EXPIRY_ZSET, {member: 0})

    assert manager.cleanup_expired_sandboxes() == 0
    assert deleted == []
    assert manager.redis_client.sorted_sets[manager.SANDBOX_EXPIRY_ZSET][member] > 0

from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase

from redis.exceptions import RedisError

from app.services.runtime_lease_service import RuntimeLeaseService


class _FailingLock:
    def __init__(self, calls: list[str], name: str) -> None:
        self._calls = calls
        self._name = name

    async def acquire(self, **kwargs):
        del kwargs
        self._calls.append(self._name)
        raise RedisError("redis unavailable")


class _FakeRedisClient:
    def __init__(self) -> None:
        self.lock_calls: list[str] = []

    def lock(self, name: str, **kwargs):
        del kwargs
        return _FailingLock(self.lock_calls, name)


class RuntimeLeaseServiceTests(IsolatedAsyncioTestCase):
    async def test_uses_local_cooldown_after_redis_failure(self) -> None:
        client = _FakeRedisClient()
        cache = SimpleNamespace(client=client)
        service = RuntimeLeaseService(cache, allow_best_effort_fallback=True)

        first = await service.acquire("runtime:test", ttl_seconds=30)
        second = await service.acquire("runtime:test", ttl_seconds=30)

        self.assertIsNotNone(first)
        self.assertIsNotNone(second)
        self.assertEqual(client.lock_calls, ["runtime:test"])

    async def test_returns_none_during_cooldown_when_fallback_disabled(self) -> None:
        client = _FakeRedisClient()
        cache = SimpleNamespace(client=client)
        service = RuntimeLeaseService(cache, allow_best_effort_fallback=False)

        first = await service.acquire("runtime:test", ttl_seconds=30)
        second = await service.acquire("runtime:test", ttl_seconds=30)

        self.assertIsNone(first)
        self.assertIsNone(second)
        self.assertEqual(client.lock_calls, ["runtime:test"])

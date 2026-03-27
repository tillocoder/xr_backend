from __future__ import annotations

import time
from collections import defaultdict

from redis.exceptions import RedisError

from app.services.cache import RedisCache


class PresenceService:
    def __init__(
        self,
        cache: RedisCache,
        *,
        ttl_seconds: int,
        refresh_interval_seconds: int,
    ) -> None:
        self._cache = cache
        self._ttl_seconds = max(15, int(ttl_seconds))
        self._refresh_interval_seconds = max(5, int(refresh_interval_seconds))
        self._local_connections: dict[str, dict[str, float]] = defaultdict(dict)

    @property
    def refresh_interval_seconds(self) -> int:
        return self._refresh_interval_seconds

    def _presence_key(self, user_id: str) -> str:
        return f"presence:user:{user_id}"

    def _prune_local(self, user_id: str, *, now: float) -> dict[str, float]:
        connections = self._local_connections.get(user_id, {})
        if not connections:
            return {}
        stale_ids = [
            connection_id
            for connection_id, expires_at in connections.items()
            if expires_at <= now
        ]
        for connection_id in stale_ids:
            connections.pop(connection_id, None)
        if not connections:
            self._local_connections.pop(user_id, None)
            return {}
        return connections

    async def connect(self, *, user_id: str, connection_id: str) -> bool:
        now = time.time()
        expires_at = now + self._ttl_seconds
        key = self._presence_key(user_id)
        try:
            pipeline = self._cache.client.pipeline(transaction=False)
            pipeline.zremrangebyscore(key, 0, now)
            pipeline.zadd(key, {connection_id: expires_at})
            pipeline.expire(key, self._ttl_seconds * 3)
            pipeline.zcard(key)
            _removed, _updated, _expire_set, active_count = await pipeline.execute()
            return int(active_count or 0) == 1
        except RedisError:
            connections = self._prune_local(user_id, now=now)
            connections[connection_id] = expires_at
            self._local_connections[user_id] = connections
            return len(connections) == 1

    async def refresh(self, *, user_id: str, connection_id: str) -> None:
        now = time.time()
        expires_at = now + self._ttl_seconds
        key = self._presence_key(user_id)
        try:
            pipeline = self._cache.client.pipeline(transaction=False)
            pipeline.zadd(key, {connection_id: expires_at})
            pipeline.expire(key, self._ttl_seconds * 3)
            await pipeline.execute()
            return
        except RedisError:
            connections = self._prune_local(user_id, now=now)
            connections[connection_id] = expires_at
            self._local_connections[user_id] = connections

    async def refresh_connections_by_user(
        self,
        connections_by_user: dict[str, tuple[str, ...]] | dict[str, list[str]],
    ) -> None:
        normalized: dict[str, list[str]] = {}
        for user_id, connection_ids in connections_by_user.items():
            normalized_user_id = str(user_id or "").strip()
            if not normalized_user_id:
                continue
            items = [
                str(connection_id).strip()
                for connection_id in connection_ids
                if str(connection_id).strip()
            ]
            if items:
                normalized[normalized_user_id] = items
        if not normalized:
            return

        now = time.time()
        expires_at = now + self._ttl_seconds
        try:
            pipeline = self._cache.client.pipeline(transaction=False)
            for user_id, connection_ids in normalized.items():
                key = self._presence_key(user_id)
                pipeline.zremrangebyscore(key, 0, now)
                pipeline.zadd(key, {connection_id: expires_at for connection_id in connection_ids})
                pipeline.expire(key, self._ttl_seconds * 3)
            await pipeline.execute()
            return
        except RedisError:
            for user_id, connection_ids in normalized.items():
                connections = self._prune_local(user_id, now=now)
                for connection_id in connection_ids:
                    connections[connection_id] = expires_at
                self._local_connections[user_id] = connections

    async def disconnect(self, *, user_id: str, connection_id: str) -> bool:
        now = time.time()
        key = self._presence_key(user_id)
        try:
            pipeline = self._cache.client.pipeline(transaction=False)
            pipeline.zrem(key, connection_id)
            pipeline.zremrangebyscore(key, 0, now)
            pipeline.zcard(key)
            _removed, _pruned, active_count = await pipeline.execute()
            if int(active_count or 0) <= 0:
                try:
                    await self._cache.client.delete(key)
                except RedisError:
                    pass
                return True
            return False
        except RedisError:
            connections = self._prune_local(user_id, now=now)
            connections.pop(connection_id, None)
            if not connections:
                self._local_connections.pop(user_id, None)
                return True
            self._local_connections[user_id] = connections
            return False

    async def is_online(self, user_id: str) -> bool:
        now = time.time()
        key = self._presence_key(user_id)
        try:
            pipeline = self._cache.client.pipeline(transaction=False)
            pipeline.zremrangebyscore(key, 0, now)
            pipeline.zcard(key)
            _removed, active_count = await pipeline.execute()
            return int(active_count or 0) > 0
        except RedisError:
            return bool(self._prune_local(user_id, now=now))

from __future__ import annotations

import json
import time

import redis.asyncio as redis
from redis.exceptions import RedisError

from app.core.config import get_settings


class RedisCache:
    def __init__(self, url: str | None = None):
        settings = get_settings()
        self._json_fallback_cache: dict[str, tuple[float, str]] = {}
        self._hash_fallback_cache: dict[str, tuple[float, dict[str, int]]] = {}
        self._int_fallback_cache: dict[str, tuple[float, int]] = {}
        self._counter_fallback_cache: dict[str, tuple[float, int]] = {}
        self._redis_disabled_until = 0.0
        self._redis_retry_after_seconds = 15.0
        self._client = redis.from_url(
            url or settings.redis_url,
            decode_responses=True,
            socket_connect_timeout=settings.redis_socket_connect_timeout_seconds,
            socket_timeout=settings.redis_socket_timeout_seconds,
            retry_on_timeout=False,
        )

    @property
    def client(self) -> redis.Redis:
        return self._client

    def _redis_available(self) -> bool:
        return time.monotonic() >= self._redis_disabled_until

    def _mark_redis_unavailable(self) -> None:
        self._redis_disabled_until = time.monotonic() + self._redis_retry_after_seconds

    def _set_local_json(self, key: str, payload: dict | list, ttl_seconds: int) -> None:
        expires_at = time.monotonic() + max(1, int(ttl_seconds))
        self._json_fallback_cache[key] = (
            expires_at,
            json.dumps(payload, separators=(",", ":")),
        )

    def _get_local_json(self, key: str) -> dict | list | None:
        cached = self._json_fallback_cache.get(key)
        if cached is None:
            return None
        expires_at, raw = cached
        if time.monotonic() >= expires_at:
            self._json_fallback_cache.pop(key, None)
            return None
        try:
            return json.loads(raw)
        except Exception:
            self._json_fallback_cache.pop(key, None)
            return None

    def _set_local_hash(self, key: str, mapping: dict[str, int], ttl_seconds: int) -> None:
        expires_at = time.monotonic() + max(1, int(ttl_seconds))
        self._hash_fallback_cache[key] = (
            expires_at,
            {str(field): int(value) for field, value in mapping.items()},
        )

    def _get_local_hash(self, key: str) -> dict[str, int] | None:
        cached = self._hash_fallback_cache.get(key)
        if cached is None:
            return None
        expires_at, mapping = cached
        if time.monotonic() >= expires_at:
            self._hash_fallback_cache.pop(key, None)
            return None
        return dict(mapping)

    def _bump_local_hash_field(
        self,
        key: str,
        field: str,
        delta: int,
        ttl_seconds: int,
    ) -> dict[str, int]:
        current = self._get_local_hash(key) or {}
        next_value = int(current.get(field, 0)) + int(delta)
        if next_value <= 0:
            current.pop(field, None)
        else:
            current[field] = next_value
        self._set_local_hash(key, current, ttl_seconds)
        return current

    def _set_local_int(self, key: str, value: int, ttl_seconds: int) -> None:
        expires_at = time.monotonic() + max(1, int(ttl_seconds))
        self._int_fallback_cache[key] = (expires_at, int(value))

    def _get_local_int(self, key: str) -> int | None:
        cached = self._int_fallback_cache.get(key)
        if cached is None:
            return None
        expires_at, value = cached
        if time.monotonic() >= expires_at:
            self._int_fallback_cache.pop(key, None)
            return None
        return int(value)

    def _increment_local(self, key: str, *, ttl_seconds: int) -> tuple[int, int]:
        now = time.monotonic()
        expires_at, count = self._counter_fallback_cache.get(key, (0.0, 0))
        if now >= expires_at:
            expires_at = now + max(1, int(ttl_seconds))
            count = 0
        count += 1
        self._counter_fallback_cache[key] = (expires_at, count)
        return count, max(0, int(expires_at - now))

    async def get_json(self, key: str) -> dict | list | None:
        if not self._redis_available():
            return self._get_local_json(key)
        try:
            raw = await self._client.get(key)
        except RedisError:
            self._mark_redis_unavailable()
            return self._get_local_json(key)
        if raw is None:
            return self._get_local_json(key)
        try:
            return json.loads(raw)
        except Exception:
            return None

    async def set_json(
        self,
        key: str,
        payload: dict | list,
        ttl_seconds: int = 120,
    ) -> None:
        self._set_local_json(key, payload, ttl_seconds)
        if not self._redis_available():
            return
        try:
            await self._client.set(
                key,
                json.dumps(payload, separators=(",", ":")),
                ex=ttl_seconds,
            )
        except RedisError:
            self._mark_redis_unavailable()
            return

    async def delete_json(self, key: str) -> None:
        self._json_fallback_cache.pop(key, None)
        if not self._redis_available():
            return
        try:
            await self._client.delete(key)
        except RedisError:
            self._mark_redis_unavailable()

    async def delete_json_prefix(self, prefix: str) -> None:
        normalized_prefix = prefix.strip()
        if not normalized_prefix:
            return
        for key in list(self._json_fallback_cache.keys()):
            if key.startswith(normalized_prefix):
                self._json_fallback_cache.pop(key, None)
        if not self._redis_available():
            return
        try:
            batch: list[str] = []
            async for key in self._client.scan_iter(
                match=f"{normalized_prefix}*",
                count=100,
            ):
                batch.append(str(key))
                if len(batch) >= 100:
                    await self._client.delete(*batch)
                    batch.clear()
            if batch:
                await self._client.delete(*batch)
        except RedisError:
            self._mark_redis_unavailable()

    async def get_profile(self, user_id: str) -> dict | None:
        payload = await self.get_json(f"profile:{user_id}")
        return payload if isinstance(payload, dict) else None

    async def set_profile(self, user_id: str, payload: dict, ttl_seconds: int = 300) -> None:
        await self.set_json(f"profile:{user_id}", payload, ttl_seconds=ttl_seconds)

    async def get_post_reaction_counts(self, post_id: str) -> dict[str, int] | None:
        key = f"post:{post_id}:reaction_counts"
        if not self._redis_available():
            return self._get_local_hash(key)
        try:
            raw = await self._client.hgetall(key)
        except RedisError:
            self._mark_redis_unavailable()
            return self._get_local_hash(key)
        if not raw:
            return self._get_local_hash(key)
        counts = {field: int(value) for field, value in raw.items()}
        self._set_local_hash(key, counts, ttl_seconds=120)
        return counts

    async def get_post_reaction_counts_many(
        self,
        post_ids: list[str],
        *,
        ttl_seconds: int = 120,
    ) -> dict[str, dict[str, int]]:
        normalized_ids = [post_id.strip() for post_id in post_ids if post_id and post_id.strip()]
        if not normalized_ids:
            return {}
        if not self._redis_available():
            return {
                post_id: counts
                for post_id in normalized_ids
                if (counts := self._get_local_hash(f"post:{post_id}:reaction_counts")) is not None
            }
        pipeline = self._client.pipeline(transaction=False)
        for post_id in normalized_ids:
            pipeline.hgetall(f"post:{post_id}:reaction_counts")
        try:
            rows = await pipeline.execute()
        except RedisError:
            self._mark_redis_unavailable()
            return {
                post_id: counts
                for post_id in normalized_ids
                if (counts := self._get_local_hash(f"post:{post_id}:reaction_counts")) is not None
            }
        out: dict[str, dict[str, int]] = {}
        for post_id, raw in zip(normalized_ids, rows):
            key = f"post:{post_id}:reaction_counts"
            if raw:
                counts = {field: int(value) for field, value in raw.items()}
                self._set_local_hash(key, counts, ttl_seconds=ttl_seconds)
                out[post_id] = counts
                continue
            fallback = self._get_local_hash(key)
            if fallback is not None:
                out[post_id] = fallback
        return out

    async def set_post_reaction_counts(
        self,
        post_id: str,
        counts: dict[str, int],
        ttl_seconds: int = 120,
    ) -> None:
        key = f"post:{post_id}:reaction_counts"
        self._set_local_hash(key, counts, ttl_seconds)
        if not self._redis_available():
            return
        try:
            if not counts:
                await self._client.delete(key)
                return
            await self._client.hset(key, mapping=counts)
            await self._client.expire(key, ttl_seconds)
        except RedisError:
            self._mark_redis_unavailable()
            return

    async def bump_post_reaction_count(
        self,
        post_id: str,
        reaction_type: str,
        delta: int,
        ttl_seconds: int = 120,
    ) -> None:
        key = f"post:{post_id}:reaction_counts"
        self._bump_local_hash_field(key, reaction_type, delta, ttl_seconds)
        if not self._redis_available():
            return
        try:
            await self._client.hincrby(key, reaction_type, delta)
            await self._client.expire(key, ttl_seconds)
        except RedisError:
            self._mark_redis_unavailable()
            return

    async def get_unread_total(self, user_id: str) -> int | None:
        key = f"user:{user_id}:unread_total"
        if not self._redis_available():
            return self._get_local_int(key)
        try:
            raw = await self._client.get(key)
        except RedisError:
            self._mark_redis_unavailable()
            return self._get_local_int(key)
        if raw is None:
            return self._get_local_int(key)
        value = int(raw)
        self._set_local_int(key, value, ttl_seconds=120)
        return value

    async def set_unread_total(self, user_id: str, value: int, ttl_seconds: int = 120) -> None:
        key = f"user:{user_id}:unread_total"
        self._set_local_int(key, value, ttl_seconds)
        if not self._redis_available():
            return
        try:
            await self._client.set(key, value, ex=ttl_seconds)
        except RedisError:
            self._mark_redis_unavailable()
            return

    async def ping(self) -> bool:
        if not self._redis_available():
            return False
        try:
            return bool(await self._client.ping())
        except RedisError:
            self._mark_redis_unavailable()
            return False

    async def increment(self, key: str, *, ttl_seconds: int) -> tuple[int, int]:
        if not self._redis_available():
            return self._increment_local(key, ttl_seconds=ttl_seconds)
        try:
            value = int(await self._client.incr(key))
            if value == 1:
                await self._client.expire(key, max(1, ttl_seconds))
            ttl = int(await self._client.ttl(key))
        except RedisError:
            self._mark_redis_unavailable()
            return self._increment_local(key, ttl_seconds=ttl_seconds)
        return value, max(0, ttl)

    async def close(self) -> None:
        try:
            await self._client.aclose()
        except RedisError:
            return

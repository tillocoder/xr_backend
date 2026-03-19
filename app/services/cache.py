from __future__ import annotations

import json

import redis.asyncio as redis
from redis.exceptions import RedisError

from app.core.config import get_settings


class RedisCache:
    def __init__(self, url: str | None = None):
        settings = get_settings()
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

    async def get_json(self, key: str) -> dict | list | None:
        try:
            raw = await self._client.get(key)
        except RedisError:
            return None
        if raw is None:
            return None
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
        try:
            await self._client.set(
                key,
                json.dumps(payload, separators=(",", ":")),
                ex=ttl_seconds,
            )
        except RedisError:
            return

    async def get_profile(self, user_id: str) -> dict | None:
        try:
            raw = await self._client.get(f"profile:{user_id}")
        except RedisError:
            return None
        return None if raw is None else json.loads(raw)

    async def set_profile(self, user_id: str, payload: dict, ttl_seconds: int = 300) -> None:
        try:
            await self._client.set(
                f"profile:{user_id}",
                json.dumps(payload, separators=(",", ":")),
                ex=ttl_seconds,
            )
        except RedisError:
            return

    async def get_post_reaction_counts(self, post_id: str) -> dict[str, int] | None:
        try:
            raw = await self._client.hgetall(f"post:{post_id}:reaction_counts")
        except RedisError:
            return None
        if not raw:
            return None
        return {key: int(value) for key, value in raw.items()}

    async def set_post_reaction_counts(
        self,
        post_id: str,
        counts: dict[str, int],
        ttl_seconds: int = 120,
    ) -> None:
        if not counts:
            return
        key = f"post:{post_id}:reaction_counts"
        try:
            await self._client.hset(key, mapping=counts)
            await self._client.expire(key, ttl_seconds)
        except RedisError:
            return

    async def bump_post_reaction_count(
        self,
        post_id: str,
        reaction_type: str,
        delta: int,
        ttl_seconds: int = 120,
    ) -> None:
        key = f"post:{post_id}:reaction_counts"
        try:
            await self._client.hincrby(key, reaction_type, delta)
            await self._client.expire(key, ttl_seconds)
        except RedisError:
            return

    async def get_unread_total(self, user_id: str) -> int | None:
        try:
            raw = await self._client.get(f"user:{user_id}:unread_total")
        except RedisError:
            return None
        return None if raw is None else int(raw)

    async def set_unread_total(self, user_id: str, value: int, ttl_seconds: int = 120) -> None:
        try:
            await self._client.set(f"user:{user_id}:unread_total", value, ex=ttl_seconds)
        except RedisError:
            return

    async def ping(self) -> bool:
        try:
            return bool(await self._client.ping())
        except RedisError:
            return False

    async def increment(self, key: str, *, ttl_seconds: int) -> tuple[int, int]:
        try:
            value = int(await self._client.incr(key))
            if value == 1:
                await self._client.expire(key, max(1, ttl_seconds))
            ttl = int(await self._client.ttl(key))
        except RedisError:
            return 0, 0
        return value, max(0, ttl)

    async def close(self) -> None:
        try:
            await self._client.aclose()
        except RedisError:
            return

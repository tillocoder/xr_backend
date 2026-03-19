from __future__ import annotations

from dataclasses import dataclass

from app.services.cache import RedisCache


@dataclass(slots=True, frozen=True)
class RateLimitDecision:
    allowed: bool
    limit: int
    remaining: int
    retry_after_seconds: int


class RedisRateLimiter:
    def __init__(self, cache: RedisCache, *, window_seconds: int) -> None:
        self._cache = cache
        self._window_seconds = max(1, int(window_seconds))

    async def check(self, *, key: str, limit: int) -> RateLimitDecision:
        normalized_limit = max(1, int(limit))
        count, ttl = await self._cache.increment(key, ttl_seconds=self._window_seconds)
        if count <= 0:
            return RateLimitDecision(
                allowed=True,
                limit=normalized_limit,
                remaining=normalized_limit,
                retry_after_seconds=0,
            )

        remaining = max(0, normalized_limit - count)
        if count > normalized_limit:
            return RateLimitDecision(
                allowed=False,
                limit=normalized_limit,
                remaining=0,
                retry_after_seconds=max(1, ttl or self._window_seconds),
            )

        return RateLimitDecision(
            allowed=True,
            limit=normalized_limit,
            remaining=remaining,
            retry_after_seconds=max(0, ttl),
        )

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

from redis.asyncio.lock import Lock
from redis.exceptions import RedisError

from app.services.cache import RedisCache


logger = logging.getLogger(__name__)


@dataclass(slots=True)
class RuntimeLease:
    name: str
    _lock: Lock | None = None

    async def release(self) -> None:
        if self._lock is None:
            return
        try:
            await self._lock.release()
        except RedisError:
            logger.warning("runtime_lease_release_failed", extra={"lease": self.name})


class RuntimeLeaseService:
    def __init__(
        self,
        cache: RedisCache,
        *,
        allow_best_effort_fallback: bool = True,
    ) -> None:
        self._cache = cache
        self._allow_best_effort_fallback = bool(allow_best_effort_fallback)
        self._next_best_effort_warning_at = 0.0
        self._warning_interval_seconds = 60.0

    def _warn_best_effort_fallback(self, normalized_name: str) -> None:
        now = time.monotonic()
        if now < self._next_best_effort_warning_at:
            return
        self._next_best_effort_warning_at = now + self._warning_interval_seconds
        logger.warning(
            "runtime_lease_best_effort_fallback",
            extra={"lease": normalized_name},
        )

    async def acquire(
        self,
        name: str,
        *,
        ttl_seconds: int,
        blocking_timeout_seconds: float = 0.0,
        best_effort_on_redis_error: bool | None = None,
    ) -> RuntimeLease | None:
        normalized_name = str(name or "").strip()
        if not normalized_name:
            raise ValueError("Lease name is required.")

        allow_best_effort_fallback = (
            self._allow_best_effort_fallback
            if best_effort_on_redis_error is None
            else bool(best_effort_on_redis_error)
        )
        lock = self._cache.client.lock(
            normalized_name,
            timeout=max(5, int(ttl_seconds)),
            thread_local=False,
        )
        try:
            acquired = await lock.acquire(
                blocking=blocking_timeout_seconds > 0,
                blocking_timeout=max(0.1, float(blocking_timeout_seconds))
                if blocking_timeout_seconds > 0
                else None,
            )
        except RedisError:
            if not allow_best_effort_fallback:
                logger.warning(
                    "runtime_lease_unavailable",
                    extra={"lease": normalized_name},
                )
                return None
            self._warn_best_effort_fallback(normalized_name)
            return RuntimeLease(name=normalized_name)

        if not acquired:
            return None
        return RuntimeLease(name=normalized_name, _lock=lock)

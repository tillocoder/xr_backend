from __future__ import annotations

from time import perf_counter

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.domain.system.models import DependencyStatus
from app.services.cache import RedisCache


class DatabaseHealthProbe:
    name = "postgres"

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def probe(self) -> DependencyStatus:
        started_at = perf_counter()
        try:
            async with self._engine.connect() as connection:
                await connection.execute(text("SELECT 1"))
        except Exception as exc:
            return DependencyStatus(
                name=self.name,
                ok=False,
                latency_ms=(perf_counter() - started_at) * 1000,
                detail=str(exc),
            )
        return DependencyStatus(
            name=self.name,
            ok=True,
            latency_ms=(perf_counter() - started_at) * 1000,
        )


class RedisHealthProbe:
    name = "redis"

    def __init__(self, cache: RedisCache) -> None:
        self._cache = cache

    async def probe(self) -> DependencyStatus:
        started_at = perf_counter()
        ok = await self._cache.ping()
        return DependencyStatus(
            name=self.name,
            ok=ok,
            latency_ms=(perf_counter() - started_at) * 1000,
            detail="" if ok else "Redis ping failed.",
        )

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Awaitable, Callable

import redis.asyncio as redis
from redis.exceptions import RedisError

from app.core.config import get_settings


TopicHandler = Callable[[str, dict], Awaitable[None]]

logger = logging.getLogger(__name__)


class RedisEventBus:
    def __init__(
        self,
        url: str,
        *,
        allow_local_fallback: bool = True,
        reconnect_delay_seconds: float | None = None,
    ):
        settings = get_settings()
        self._client = redis.from_url(
            url,
            decode_responses=True,
            socket_connect_timeout=settings.redis_socket_connect_timeout_seconds,
            socket_timeout=settings.redis_socket_timeout_seconds,
            retry_on_timeout=False,
        )
        self._task: asyncio.Task | None = None
        self._handler: TopicHandler | None = None
        self._allow_local_fallback = bool(allow_local_fallback)
        self._redis_disabled_until = 0.0
        self._redis_retry_after_seconds = max(1.0, float(settings.redis_retry_after_seconds))
        self._redis_retry_after_max_seconds = max(
            self._redis_retry_after_seconds,
            float(settings.redis_pubsub_reconnect_max_delay_seconds),
        )
        self._redis_current_retry_after_seconds = self._redis_retry_after_seconds
        self._next_warning_at = 0.0
        self._warning_interval_seconds = max(60.0, self._redis_retry_after_seconds)
        self._reconnect_delay_seconds = max(
            0.2,
            float(
                reconnect_delay_seconds
                if reconnect_delay_seconds is not None
                else settings.redis_pubsub_reconnect_delay_seconds
            ),
        )
        self._max_reconnect_delay_seconds = max(
            self._reconnect_delay_seconds,
            float(settings.redis_pubsub_reconnect_max_delay_seconds),
        )

    def _redis_available(self) -> bool:
        return time.monotonic() >= self._redis_disabled_until

    def _mark_redis_unavailable(self) -> None:
        self._redis_disabled_until = time.monotonic() + self._redis_current_retry_after_seconds
        self._redis_current_retry_after_seconds = min(
            self._redis_retry_after_max_seconds,
            self._redis_current_retry_after_seconds * 2,
        )

    def _mark_redis_available(self) -> None:
        self._redis_disabled_until = 0.0
        self._redis_current_retry_after_seconds = self._redis_retry_after_seconds
        self._next_warning_at = 0.0

    def _warn_redis_issue(self, message: str) -> None:
        now = time.monotonic()
        if now < self._next_warning_at:
            return
        self._next_warning_at = now + self._warning_interval_seconds
        logger.warning(
            message,
            extra={
                "retryAfterSeconds": round(self._redis_current_retry_after_seconds, 2),
                "localFallback": self._allow_local_fallback,
            },
        )

    async def publish(self, topic: str, payload: dict) -> None:
        if not self._redis_available():
            if self._allow_local_fallback and self._handler is not None:
                await self._handler(topic, payload)
            return
        try:
            await self._client.publish(topic, json.dumps(payload, separators=(",", ":")))
            self._mark_redis_available()
        except RedisError:
            self._mark_redis_unavailable()
            if self._allow_local_fallback and self._handler is not None:
                await self._handler(topic, payload)
                return
            raise

    async def start(self, handler: TopicHandler) -> None:
        self._handler = handler
        if self._task is not None:
            return
        self._task = asyncio.create_task(self._listen(handler))

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        await self._client.aclose()

    async def _listen(self, handler: TopicHandler) -> None:
        reconnect_delay = self._reconnect_delay_seconds
        while True:
            if not self._redis_available():
                await asyncio.sleep(
                    max(0.2, self._redis_disabled_until - time.monotonic()),
                )
            pubsub = self._client.pubsub()
            try:
                try:
                    await pubsub.psubscribe("user:*", "room:*", "feed:*", "presence:*")
                    reconnect_delay = self._reconnect_delay_seconds
                    self._mark_redis_available()
                except RedisError:
                    self._mark_redis_unavailable()
                    self._warn_redis_issue("redis_event_bus_subscribe_failed")
                    await asyncio.sleep(reconnect_delay)
                    reconnect_delay = min(
                        self._max_reconnect_delay_seconds,
                        reconnect_delay * 2,
                    )
                    continue
                while True:
                    try:
                        message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
                    except RedisError:
                        self._mark_redis_unavailable()
                        self._warn_redis_issue("redis_event_bus_listen_failed")
                        break
                    if not message:
                        await asyncio.sleep(0.05)
                        continue
                    reconnect_delay = self._reconnect_delay_seconds
                    channel = message["channel"]
                    payload = json.loads(message["data"])
                    await handler(channel, payload)
            finally:
                try:
                    await pubsub.aclose()
                except RedisError:
                    pass
            await asyncio.sleep(reconnect_delay)
            reconnect_delay = min(
                self._max_reconnect_delay_seconds,
                reconnect_delay * 2,
            )

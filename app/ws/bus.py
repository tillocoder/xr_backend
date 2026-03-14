from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable

import redis.asyncio as redis
from redis.exceptions import RedisError

from app.core.config import get_settings


TopicHandler = Callable[[str, dict], Awaitable[None]]


class RedisEventBus:
    def __init__(self, url: str):
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

    async def publish(self, topic: str, payload: dict) -> None:
        try:
            await self._client.publish(topic, json.dumps(payload, separators=(",", ":")))
        except RedisError:
            if self._handler is not None:
                await self._handler(topic, payload)

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
        pubsub = self._client.pubsub()
        try:
            try:
                await pubsub.psubscribe("user:*", "room:*", "feed:*")
            except RedisError:
                return
            while True:
                try:
                    message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
                except RedisError:
                    return
                if not message:
                    await asyncio.sleep(0.05)
                    continue
                channel = message["channel"]
                payload = json.loads(message["data"])
                await handler(channel, payload)
        finally:
            try:
                await pubsub.aclose()
            except RedisError:
                pass

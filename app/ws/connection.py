from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass
from collections.abc import Awaitable, Callable
from typing import Literal
from uuid import uuid4

from fastapi import WebSocket
from starlette.websockets import WebSocketState


EnqueueOutcome = Literal["queued", "dropped", "overflow", "closed"]
CloseCallback = Callable[[str], Awaitable[None]]

_DROPPABLE_EVENT_TYPES = frozenset(
    {
        "chat.presence",
        "chat.typing",
    }
)


@dataclass(slots=True, frozen=True)
class OutboundMessage:
    body: str
    event_type: str = ""
    coalesce_key: str = ""


class ManagedWebSocketConnection:
    def __init__(
        self,
        *,
        ws: WebSocket,
        user_id: str,
        max_pending_messages: int,
        send_timeout_seconds: float,
        on_unexpected_close: CloseCallback | None = None,
    ) -> None:
        self._ws = ws
        self._user_id = user_id
        self._send_timeout_seconds = max(0.5, float(send_timeout_seconds))
        self._queue: asyncio.Queue[OutboundMessage] = asyncio.Queue(
            maxsize=max(8, int(max_pending_messages))
        )
        self._on_unexpected_close = on_unexpected_close
        self._writer_task: asyncio.Task | None = None
        self._closed = False
        self._managed_shutdown = False
        self._close_notified = False
        self._connection_id = uuid4().hex

    @property
    def connection_id(self) -> str:
        return self._connection_id

    @property
    def user_id(self) -> str:
        return self._user_id

    @property
    def pending_messages(self) -> int:
        return self._queue.qsize()

    def should_reap(self) -> bool:
        if self._closed:
            return True
        return (
            self._ws.client_state is WebSocketState.DISCONNECTED
            or self._ws.application_state is WebSocketState.DISCONNECTED
        )

    async def accept(self) -> None:
        await self._ws.accept()
        self._writer_task = asyncio.create_task(self._writer_loop())

    def enqueue(self, payload: OutboundMessage) -> EnqueueOutcome:
        if self._closed:
            return "closed"
        if payload.coalesce_key:
            queued = self._replace_coalesced(payload)
            if queued:
                return "queued"
        try:
            self._queue.put_nowait(payload)
            return "queued"
        except asyncio.QueueFull:
            if payload.event_type in _DROPPABLE_EVENT_TYPES:
                return "dropped"
            return "overflow"

    def _replace_coalesced(self, payload: OutboundMessage) -> bool:
        queued_items = getattr(self._queue, "_queue", None)
        if queued_items is None:
            return False
        for index in range(len(queued_items) - 1, -1, -1):
            queued_payload = queued_items[index]
            if (
                isinstance(queued_payload, OutboundMessage)
                and queued_payload.coalesce_key == payload.coalesce_key
            ):
                queued_items[index] = payload
                return True
        return False

    async def close(self, *, code: int = 1000, reason: str | None = None) -> None:
        if self._closed:
            return
        self._closed = True
        self._managed_shutdown = True
        writer_task = self._writer_task
        if writer_task is not None:
            writer_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await writer_task
        with contextlib.suppress(Exception):
            await asyncio.wait_for(
                self._ws.close(code=code, reason=reason),
                timeout=self._send_timeout_seconds,
            )

    async def _writer_loop(self) -> None:
        try:
            while True:
                payload = await self._queue.get()
                await asyncio.wait_for(
                    self._ws.send_text(payload.body),
                    timeout=self._send_timeout_seconds,
                )
        except asyncio.CancelledError:
            raise
        except Exception:
            self._closed = True
        finally:
            if not self._managed_shutdown:
                await self._notify_unexpected_close()

    async def _notify_unexpected_close(self) -> None:
        if self._close_notified:
            return
        self._close_notified = True
        if self._on_unexpected_close is None:
            return
        await self._on_unexpected_close(self._connection_id)

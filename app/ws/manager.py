from __future__ import annotations

import asyncio
import json
from collections import defaultdict
from typing import Any, Literal

from fastapi import WebSocket
from prometheus_client import Counter, Gauge, Histogram

from app.ws.connection import ManagedWebSocketConnection, OutboundMessage


WS_ACTIVE_CONNECTIONS = Gauge(
    "xr_backend_ws_active_connections",
    "Currently active websocket connections on this application instance.",
)
WS_ACTIVE_USERS = Gauge(
    "xr_backend_ws_active_users",
    "Currently active websocket users on this application instance.",
)
WS_OUTBOUND_EVENTS = Counter(
    "xr_backend_ws_outbound_events_total",
    "Outbound websocket enqueue outcomes.",
    ("topic_kind", "outcome"),
)
WS_FANOUT_SIZE = Histogram(
    "xr_backend_ws_fanout_targets",
    "Number of websocket targets for each dispatch.",
    ("topic_kind",),
    buckets=(1, 2, 4, 8, 16, 32, 64, 128, 256, 512, 1024, 2048, 4096, 8192, 16384),
)
WS_REAPED_CONNECTIONS = Counter(
    "xr_backend_ws_reaped_connections_total",
    "Connections reaped after their underlying websocket was already disconnected.",
)

SubscriptionResult = Literal[
    "subscribed",
    "already_subscribed",
    "limit_reached",
    "missing_connection",
]


class ConnectionManager:
    def __init__(
        self,
        *,
        send_timeout_seconds: float,
        max_pending_messages: int,
        max_rooms_per_connection: int,
        max_topics_per_connection: int,
    ) -> None:
        self._connections: dict[str, ManagedWebSocketConnection] = {}
        self._user_connections: dict[str, set[str]] = defaultdict(set)
        self._room_connections: dict[str, set[str]] = defaultdict(set)
        self._topic_connections: dict[str, set[str]] = defaultdict(set)
        self._connection_rooms: dict[str, set[str]] = defaultdict(set)
        self._connection_topics: dict[str, set[str]] = defaultdict(set)
        self._send_timeout_seconds = max(0.5, float(send_timeout_seconds))
        self._max_pending_messages = max(8, int(max_pending_messages))
        self._max_rooms_per_connection = max(1, int(max_rooms_per_connection))
        self._max_topics_per_connection = max(1, int(max_topics_per_connection))

    async def connect_user(self, user_id: str, ws: WebSocket) -> ManagedWebSocketConnection:
        connection = ManagedWebSocketConnection(
            ws=ws,
            user_id=user_id,
            max_pending_messages=self._max_pending_messages,
            send_timeout_seconds=self._send_timeout_seconds,
            on_unexpected_close=self._handle_unexpected_close,
        )
        await connection.accept()
        self._connections[connection.connection_id] = connection
        self._user_connections[user_id].add(connection.connection_id)
        self._refresh_metrics()
        return connection

    async def disconnect_connection(
        self,
        connection_id: str,
        *,
        code: int = 1000,
        reason: str | None = None,
    ) -> None:
        await self._remove_connection(connection_id, close_socket=True, code=code, reason=reason)

    async def _handle_unexpected_close(self, connection_id: str) -> None:
        await self._remove_connection(connection_id, close_socket=False)

    async def _remove_connection(
        self,
        connection_id: str,
        *,
        close_socket: bool,
        code: int = 1000,
        reason: str | None = None,
    ) -> None:
        connection = self._connections.pop(connection_id, None)
        if connection is None:
            return

        user_set = self._user_connections.get(connection.user_id)
        if user_set is not None:
            user_set.discard(connection_id)
            if not user_set:
                self._user_connections.pop(connection.user_id, None)

        for room_id in self._connection_rooms.pop(connection_id, set()):
            members = self._room_connections.get(room_id)
            if members is not None:
                members.discard(connection_id)
                if not members:
                    self._room_connections.pop(room_id, None)

        for topic in self._connection_topics.pop(connection_id, set()):
            members = self._topic_connections.get(topic)
            if members is not None:
                members.discard(connection_id)
                if not members:
                    self._topic_connections.pop(topic, None)

        self._refresh_metrics()
        if close_socket:
            await connection.close(code=code, reason=reason)

    def connection_exists(self, connection_id: str) -> bool:
        return connection_id in self._connections

    def snapshot_connections_by_user(self) -> dict[str, tuple[str, ...]]:
        return {
            user_id: tuple(connection_ids)
            for user_id, connection_ids in self._user_connections.items()
            if connection_ids
        }

    def join_room(self, room_id: str, connection_id: str) -> SubscriptionResult:
        if connection_id not in self._connections:
            return "missing_connection"
        rooms = self._connection_rooms[connection_id]
        if room_id in rooms:
            return "already_subscribed"
        if len(rooms) >= self._max_rooms_per_connection:
            return "limit_reached"
        self._room_connections[room_id].add(connection_id)
        rooms.add(room_id)
        return "subscribed"

    def leave_room(self, room_id: str, connection_id: str) -> None:
        members = self._room_connections.get(room_id)
        if members is not None:
            members.discard(connection_id)
            if not members:
                self._room_connections.pop(room_id, None)
        self._connection_rooms.get(connection_id, set()).discard(room_id)

    def subscribe_topic(self, topic: str, connection_id: str) -> SubscriptionResult:
        if connection_id not in self._connections:
            return "missing_connection"
        topics = self._connection_topics[connection_id]
        if topic in topics:
            return "already_subscribed"
        if len(topics) >= self._max_topics_per_connection:
            return "limit_reached"
        self._topic_connections[topic].add(connection_id)
        topics.add(topic)
        return "subscribed"

    def unsubscribe_topic(self, topic: str, connection_id: str) -> None:
        members = self._topic_connections.get(topic)
        if members is not None:
            members.discard(connection_id)
            if not members:
                self._topic_connections.pop(topic, None)
        self._connection_topics.get(connection_id, set()).discard(topic)

    def is_user_online(self, user_id: str) -> bool:
        return bool(self._user_connections.get(user_id))

    def is_user_in_room(self, user_id: str, room_id: str) -> bool:
        user_sockets = self._user_connections.get(user_id, set())
        room_sockets = self._room_connections.get(room_id, set())
        return any(connection_id in room_sockets for connection_id in user_sockets)

    async def send_to_connection(self, connection_id: str, payload: dict[str, Any]) -> None:
        await self._fanout({connection_id}, payload, topic_kind="direct")

    async def send_to_user(self, user_id: str, payload: dict[str, Any]) -> None:
        await self._fanout(
            self._user_connections.get(user_id, set()),
            payload,
            topic_kind="user",
        )

    async def send_to_room(self, room_id: str, payload: dict[str, Any]) -> None:
        await self._fanout(
            self._room_connections.get(room_id, set()),
            payload,
            topic_kind="room",
        )

    async def send_to_topic(self, topic: str, payload: dict[str, Any]) -> None:
        await self._fanout(
            self._topic_connections.get(topic, set()),
            payload,
            topic_kind="topic",
        )

    async def dispatch(self, topic: str, payload: dict[str, Any]) -> None:
        if topic.startswith("user:"):
            await self.send_to_user(topic.split(":", 1)[1], payload)
            return
        if topic.startswith("room:"):
            await self.send_to_room(topic.split(":", 1)[1], payload)
            return
        await self.send_to_topic(topic, payload)

    async def reap_stale_connections(self) -> int:
        stale_connection_ids = [
            connection_id
            for connection_id, connection in self._connections.items()
            if connection.should_reap()
        ]
        if not stale_connection_ids:
            return 0

        await asyncio.gather(
            *(
                self.disconnect_connection(
                    connection_id,
                    code=1001,
                    reason="Websocket disconnected",
                )
                for connection_id in stale_connection_ids
            ),
            return_exceptions=True,
        )
        WS_REAPED_CONNECTIONS.inc(len(stale_connection_ids))
        return len(stale_connection_ids)

    async def _fanout(
        self,
        connection_ids: set[str],
        payload: dict[str, Any],
        *,
        topic_kind: str,
    ) -> None:
        targets = list(connection_ids)
        if not targets:
            return

        WS_FANOUT_SIZE.labels(topic_kind=topic_kind).observe(len(targets))
        overflowed: list[str] = []
        serialized_payload = json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
        )
        event_type = str(payload.get("type") or "").strip()
        outbound_message = OutboundMessage(
            body=serialized_payload,
            event_type=event_type,
        )

        for connection_id in targets:
            connection = self._connections.get(connection_id)
            if connection is None:
                continue
            outcome = connection.enqueue(outbound_message)
            WS_OUTBOUND_EVENTS.labels(topic_kind=topic_kind, outcome=outcome).inc()
            if outcome in {"closed", "overflow"}:
                overflowed.append(connection_id)

        if overflowed:
            await asyncio.gather(
                *(
                    self.disconnect_connection(
                        connection_id,
                        code=1013,
                        reason="Websocket backpressure",
                    )
                    for connection_id in overflowed
                ),
                return_exceptions=True,
            )

    def _refresh_metrics(self) -> None:
        WS_ACTIVE_CONNECTIONS.set(len(self._connections))
        WS_ACTIVE_USERS.set(len(self._user_connections))

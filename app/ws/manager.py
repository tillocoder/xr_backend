from __future__ import annotations

from collections import defaultdict

from fastapi import WebSocket


class ConnectionManager:
    def __init__(self) -> None:
        self._user_connections: dict[str, set[WebSocket]] = defaultdict(set)
        self._room_connections: dict[str, set[WebSocket]] = defaultdict(set)
        self._topic_connections: dict[str, set[WebSocket]] = defaultdict(set)

    async def connect_user(self, user_id: str, ws: WebSocket) -> None:
        await ws.accept()
        self._user_connections[user_id].add(ws)

    def disconnect_user(self, user_id: str, ws: WebSocket) -> None:
        self._user_connections[user_id].discard(ws)
        for sockets in self._room_connections.values():
            sockets.discard(ws)
        for sockets in self._topic_connections.values():
            sockets.discard(ws)

    def join_room(self, room_id: str, ws: WebSocket) -> None:
        self._room_connections[room_id].add(ws)

    def leave_room(self, room_id: str, ws: WebSocket) -> None:
        self._room_connections[room_id].discard(ws)

    def subscribe_topic(self, topic: str, ws: WebSocket) -> None:
        self._topic_connections[topic].add(ws)

    def unsubscribe_topic(self, topic: str, ws: WebSocket) -> None:
        self._topic_connections[topic].discard(ws)

    def is_user_online(self, user_id: str) -> bool:
        return bool(self._user_connections.get(user_id, set()))

    def is_user_in_room(self, user_id: str, room_id: str) -> bool:
        user_sockets = self._user_connections.get(user_id, set())
        room_sockets = self._room_connections.get(room_id, set())
        return any(ws in room_sockets for ws in user_sockets)

    async def send_to_user(self, user_id: str, payload: dict) -> None:
        await self._fanout(self._user_connections.get(user_id, set()), payload)

    async def send_to_room(self, room_id: str, payload: dict) -> None:
        await self._fanout(self._room_connections.get(room_id, set()), payload)

    async def send_to_topic(self, topic: str, payload: dict) -> None:
        await self._fanout(self._topic_connections.get(topic, set()), payload)

    async def dispatch(self, topic: str, payload: dict) -> None:
        if topic.startswith("user:"):
            await self.send_to_user(topic.split(":", 1)[1], payload)
            return
        if topic.startswith("room:"):
            await self.send_to_room(topic.split(":", 1)[1], payload)
            return
        await self.send_to_topic(topic, payload)

    async def _fanout(self, sockets: set[WebSocket], payload: dict) -> None:
        dead: list[WebSocket] = []
        for ws in sockets:
            try:
                await ws.send_json(payload)
            except Exception:
                dead.append(ws)
        for ws in dead:
            for members in self._user_connections.values():
                members.discard(ws)
            for members in self._room_connections.values():
                members.discard(ws)
            for members in self._topic_connections.values():
                members.discard(ws)

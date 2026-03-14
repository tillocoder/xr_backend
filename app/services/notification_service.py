from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import SessionLocal
from app.models.entities import Notification, User
from app.schemas.notification import NotificationItemResponse, NotificationListResponse
from app.schemas.ws import WsEnvelope
from app.services.firebase_push_service import FirebasePushService
from app.services.push_token_service import PushTokenService
from app.ws.bus import RedisEventBus


class NotificationService:
    _broadcast_insert_chunk_size = 400

    def __init__(
        self,
        *,
        push_token_service: PushTokenService,
        firebase_push_service: FirebasePushService,
        bus: RedisEventBus | None = None,
    ) -> None:
        self._push_tokens = push_token_service
        self._firebase_push = firebase_push_service
        self._bus = bus

    def queue_broadcast_notification(
        self,
        *,
        kind: str,
        title: str,
        body: str,
        actor_uid: str | None = None,
        post_id: str | None = None,
        extra_payload: dict[str, object] | None = None,
    ) -> None:
        asyncio.create_task(
            self._create_broadcast_notification_with_session(
                kind=kind,
                title=title,
                body=body,
                actor_uid=actor_uid,
                post_id=post_id,
                extra_payload=extra_payload,
            )
        )

    async def _create_broadcast_notification_with_session(
        self,
        *,
        kind: str,
        title: str,
        body: str,
        actor_uid: str | None,
        post_id: str | None,
        extra_payload: dict[str, object] | None,
    ) -> None:
        try:
            async with SessionLocal() as db:
                await self.create_broadcast_notification(
                    db,
                    kind=kind,
                    title=title,
                    body=body,
                    actor_uid=actor_uid,
                    post_id=post_id,
                    extra_payload=extra_payload,
                )
        except Exception:
            return

    async def list_notifications(
        self,
        db: AsyncSession,
        *,
        user_id: str,
        limit: int,
        unread_only: bool,
    ) -> NotificationListResponse:
        normalized_limit = max(1, min(limit, 100))
        stmt = (
            select(Notification)
            .where(Notification.user_id == user_id)
            .order_by(Notification.created_at.desc())
            .limit(normalized_limit)
        )
        if unread_only:
            stmt = stmt.where(Notification.is_read.is_(False))

        rows = (await db.scalars(stmt)).all()
        actors = await self._load_actor_map(
            db,
            [
                _optional_str(row.payload_json.get("actor_uid"))
                for row in rows
            ],
        )
        unread_count = await db.scalar(
            select(func.count(Notification.id)).where(
                Notification.user_id == user_id,
                Notification.is_read.is_(False),
            )
        )
        return NotificationListResponse(
            items=[
                self._build_response(row, actors)
                for row in rows
            ],
            unread_count=int(unread_count or 0),
        )

    async def mark_read(self, db: AsyncSession, *, user_id: str, ids: list[str]) -> None:
        normalized_ids = [item.strip() for item in ids if item.strip()]
        if not normalized_ids:
            return
        await db.execute(
            update(Notification)
            .where(Notification.user_id == user_id, Notification.id.in_(normalized_ids))
            .values(is_read=True)
        )
        await db.commit()

    async def mark_all_read(self, db: AsyncSession, *, user_id: str) -> None:
        await db.execute(
            update(Notification)
            .where(Notification.user_id == user_id, Notification.is_read.is_(False))
            .values(is_read=True)
        )
        await db.commit()

    async def create_notification(
        self,
        db: AsyncSession,
        *,
        user_id: str,
        kind: str,
        title: str,
        body: str,
        actor_uid: str | None = None,
        post_id: str | None = None,
        extra_payload: dict[str, object] | None = None,
    ) -> Notification:
        payload = {
            "title": title,
            "body": body,
            "actor_uid": actor_uid,
            "post_id": post_id,
        }
        if extra_payload:
            payload.update(
                {
                    key: value
                    for key, value in extra_payload.items()
                    if value is not None
                }
            )
        notification = Notification(
            user_id=user_id,
            event_type=kind,
            payload_json=payload,
            is_read=False,
        )
        db.add(notification)
        await db.flush()
        await db.commit()
        await db.refresh(notification)

        self._queue_push(
            user_id=user_id,
            notification_id=notification.id,
            title=title,
            body=body,
            kind=kind,
            actor_uid=actor_uid,
            post_id=post_id,
            extra_payload=extra_payload,
        )
        return notification

    async def create_broadcast_notification(
        self,
        db: AsyncSession,
        *,
        kind: str,
        title: str,
        body: str,
        actor_uid: str | None = None,
        post_id: str | None = None,
        extra_payload: dict[str, object] | None = None,
    ) -> int:
        normalized_title = title.strip()
        normalized_body = body.strip()
        if not normalized_title or not normalized_body:
            return 0

        payload = {
            "title": normalized_title,
            "body": normalized_body,
            "actor_uid": actor_uid,
            "post_id": post_id,
        }
        if extra_payload:
            payload.update(
                {
                    key: value
                    for key, value in extra_payload.items()
                    if value is not None
                }
            )

        user_ids = [
            user_id.strip()
            for user_id in (await db.scalars(select(User.id))).all()
            if user_id.strip()
        ]
        if not user_ids:
            return 0

        now = datetime.now(timezone.utc)
        rows = [
            {
                "id": uuid4().hex,
                "user_id": user_id,
                "event_type": kind,
                "payload_json": payload,
                "is_read": False,
                "created_at": now,
            }
            for user_id in user_ids
        ]
        for start in range(0, len(rows), self._broadcast_insert_chunk_size):
            await db.execute(
                insert(Notification),
                rows[start : start + self._broadcast_insert_chunk_size],
            )
        await db.commit()
        await self._publish_broadcast_notifications(
            rows=rows,
            kind=kind,
            title=normalized_title,
            body=normalized_body,
            payload=payload,
            created_at=now,
        )
        return len(rows)

    def _queue_push(
        self,
        *,
        user_id: str,
        notification_id: str,
        title: str,
        body: str,
        kind: str,
        actor_uid: str | None,
        post_id: str | None,
        extra_payload: dict[str, object] | None,
    ) -> None:
        if not self._firebase_push.is_configured:
            return
        asyncio.create_task(
            self._send_push_with_session(
                user_id=user_id,
                notification_id=notification_id,
                title=title,
                body=body,
                kind=kind,
                actor_uid=actor_uid,
                post_id=post_id,
                extra_payload=extra_payload,
            )
        )

    async def _send_push_with_session(
        self,
        *,
        user_id: str,
        notification_id: str,
        title: str,
        body: str,
        kind: str,
        actor_uid: str | None,
        post_id: str | None,
        extra_payload: dict[str, object] | None,
    ) -> None:
        try:
            async with SessionLocal() as db:
                await self._send_push_if_possible(
                    db,
                    user_id=user_id,
                    notification_id=notification_id,
                    title=title,
                    body=body,
                    kind=kind,
                    actor_uid=actor_uid,
                    post_id=post_id,
                    extra_payload=extra_payload,
                )
        except Exception:
            return

    async def serialize_notification(
        self,
        db: AsyncSession,
        notification: Notification,
    ) -> dict[str, object]:
        actor_uid = _optional_str(notification.payload_json.get("actor_uid"))
        actors = await self._load_actor_map(db, [actor_uid])
        actor = actors.get(actor_uid or "")
        item = self._build_response(notification, actors)
        payload = item.model_dump(mode="json")
        payload["actorUid"] = item.actor_uid
        payload["actorDisplayName"] = item.actor_display_name
        payload["actorAvatarUrl"] = item.actor_avatar_url
        payload["actorUsername"] = actor.username if actor is not None else None
        payload["postId"] = item.post_id
        payload["createdAt"] = payload["created_at"]
        payload["readAt"] = payload["read_at"]
        payload["eventType"] = item.event_type
        peer_id = _optional_str(notification.payload_json.get("peer_id"))
        message_id = _optional_str(notification.payload_json.get("message_id"))
        message_type = _optional_str(notification.payload_json.get("message_type"))
        if peer_id is not None:
            payload["peer_id"] = peer_id
            payload["peerId"] = peer_id
        if message_id is not None:
            payload["message_id"] = message_id
            payload["messageId"] = message_id
        if message_type is not None:
            payload["message_type"] = message_type
            payload["messageType"] = message_type
        sender_display_name = _optional_str(notification.payload_json.get("sender_display_name"))
        sender_username = _optional_str(notification.payload_json.get("sender_username"))
        sender_avatar_url = _optional_str(notification.payload_json.get("sender_avatar_url"))
        if sender_display_name is not None:
            payload["sender_display_name"] = sender_display_name
            payload["senderDisplayName"] = sender_display_name
        if sender_username is not None:
            payload["sender_username"] = sender_username
            payload["senderUsername"] = sender_username
        if sender_avatar_url is not None:
            payload["sender_avatar_url"] = sender_avatar_url
            payload["senderAvatarUrl"] = sender_avatar_url
        return payload

    async def _send_push_if_possible(
        self,
        db: AsyncSession,
        *,
        user_id: str,
        notification_id: str,
        title: str,
        body: str,
        kind: str,
        actor_uid: str | None,
        post_id: str | None,
        extra_payload: dict[str, object] | None,
    ) -> None:
        if not self._firebase_push.is_configured:
            return
        tokens = await self._push_tokens.list_tokens(db, user_id=user_id)
        if not tokens:
            return
        push_data = {
            "notificationId": notification_id,
            "kind": kind,
            "actorUid": actor_uid or "",
            "postId": post_id or "",
        }
        if extra_payload:
            push_data.update(
                {
                    str(key): str(value)
                    for key, value in extra_payload.items()
                    if value is not None
                }
            )
        invalid_tokens = self._firebase_push.send_to_tokens(
            tokens=tokens,
            title=title,
            body=body,
            data=push_data,
        )
        if invalid_tokens:
            await self._push_tokens.remove_tokens(db, invalid_tokens)

    async def _publish_broadcast_notifications(
        self,
        *,
        rows: list[dict[str, object]],
        kind: str,
        title: str,
        body: str,
        payload: dict[str, object],
        created_at: datetime,
    ) -> None:
        if self._bus is None or not rows:
            return
        created_at_json = created_at.isoformat()
        for index, row in enumerate(rows, start=1):
            user_id = str(row["user_id"])
            notification_payload = {
                "id": str(row["id"]),
                "kind": kind,
                "eventType": kind,
                "title": title,
                "body": body,
                "payload": payload,
                "createdAt": created_at_json,
                "created_at": created_at_json,
                "isRead": False,
                "readAt": None,
                "read_at": None,
                **{key: value for key, value in payload.items() if value is not None},
            }
            await self._bus.publish(
                f"user:{user_id}",
                WsEnvelope(
                    type="notification.new",
                    topic=f"user:{user_id}",
                    data={"notification": notification_payload},
                ).model_dump(mode="json"),
            )
            if index % self._broadcast_insert_chunk_size == 0:
                await asyncio.sleep(0)

    async def _load_actor_map(
        self,
        db: AsyncSession,
        actor_ids: list[str | None],
    ) -> dict[str, User]:
        normalized = sorted({
            actor_id.strip()
            for actor_id in actor_ids
            if actor_id is not None and actor_id.strip()
        })
        if not normalized:
            return {}
        rows = (
            await db.scalars(
                select(User).where(User.id.in_(normalized))
            )
        ).all()
        return {row.id: row for row in rows}

    def _build_response(
        self,
        notification: Notification,
        actors: dict[str, User],
    ) -> NotificationItemResponse:
        actor_uid = _optional_str(notification.payload_json.get("actor_uid"))
        actor = actors.get(actor_uid or "")
        title = str(notification.payload_json.get("title", "")).strip()
        body = str(notification.payload_json.get("body", "")).strip()
        return NotificationItemResponse(
            id=notification.id,
            kind=notification.event_type,
            event_type=notification.event_type,
            title=title,
            body=body,
            payload={
                key: value
                for key, value in notification.payload_json.items()
                if value is not None
            },
            actor_uid=actor_uid,
            actor_display_name=actor.display_name if actor is not None else None,
            actor_avatar_url=actor.avatar_url if actor is not None else None,
            post_id=_optional_str(notification.payload_json.get("post_id")),
            created_at=notification.created_at,
            read_at=notification.created_at if notification.is_read else None,
        )


def _optional_str(value: object) -> str | None:
    normalized = str(value or "").strip()
    return normalized or None

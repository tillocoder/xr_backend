from __future__ import annotations

import base64
import json
from datetime import datetime
from pathlib import Path

from fastapi import HTTPException
from sqlalchemy import and_, delete, desc, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.entities import Chat, ChatMember, CommunityProfile, Message, User
from app.schemas.chat import ChatConversationOut, ChatMessageOut, ChatMessageReplyOut, ChatMessagesPage
from app.schemas.feed import FeedAuthor
from app.schemas.ws import WsEnvelope
from app.services.cache import RedisCache
from app.services.daily_reward_service import DailyRewardService
from app.services.notification_service import NotificationService
from app.services.user_service import ensure_user_exists
from app.ws.bus import RedisEventBus

_MEDIA_ROOT = Path(__file__).resolve().parents[2] / "media"
_daily_rewards = DailyRewardService()


def _encode_cursor(created_at: datetime, message_id: str) -> str:
    payload = {"created_at": created_at.isoformat(), "message_id": message_id}
    return base64.urlsafe_b64encode(json.dumps(payload).encode("utf-8")).decode("utf-8")


def _decode_cursor(cursor: str | None) -> tuple[datetime, str] | None:
    if not cursor:
        return None
    data = json.loads(base64.urlsafe_b64decode(cursor.encode("utf-8")).decode("utf-8"))
    return datetime.fromisoformat(data["created_at"]), data["message_id"]


def _message_preview(message: Message | None) -> str:
    if message is None:
        return ""
    return _message_preview_from_fields(
        body=message.body,
        message_type=message.message_type,
        deleted_at=message.deleted_at,
    )


def _message_preview_from_fields(
    *,
    body: str | None,
    message_type: str | None,
    deleted_at: datetime | None,
) -> str:
    if deleted_at is not None:
        return "Message deleted"
    if message_type == "voice":
        return "Voice message"
    if message_type == "image":
        return "Photo"
    return (body or "").strip()


def _serialize_chat_message(
    message: Message,
    *,
    recipient_id: str | None,
    reply_lookup: dict[str, Message] | None = None,
    public_base_url: str | None = None,
) -> ChatMessageOut:
    reply_target = None
    if message.reply_to_message_id:
        reply_target = (reply_lookup or {}).get(message.reply_to_message_id)
    return ChatMessageOut(
        id=message.id,
        chat_id=message.chat_id,
        sender_id=message.sender_id,
        recipient_id=recipient_id,
        body=message.body,
        message_type=message.message_type,
        media_url=_public_media_url(message.media_url, public_base_url),
        media_duration_ms=message.media_duration_ms,
        waveform=[float(item) for item in list(message.waveform_json or [])],
        created_at=message.created_at,
        updated_at=message.updated_at,
        deleted_at=message.deleted_at,
        read_at=None,
        reply_to=_serialize_reply_preview(
            reply_to_message_id=message.reply_to_message_id,
            reply_target=reply_target,
        ),
    )


def _serialize_reply_preview(
    *,
    reply_to_message_id: str | None,
    reply_target: Message | None,
) -> ChatMessageReplyOut | None:
    reply_id = (reply_to_message_id or "").strip()
    if not reply_id:
        return None
    return ChatMessageReplyOut(
        message_id=reply_id,
        sender_id=reply_target.sender_id if reply_target is not None else "",
        preview_text=_message_preview(reply_target),
        message_type=reply_target.message_type if reply_target is not None else "text",
        is_deleted=reply_target.deleted_at is not None if reply_target is not None else False,
    )


async def _load_reply_lookup(
    db: AsyncSession,
    messages: list[Message],
) -> dict[str, Message]:
    reply_ids = {
        message.reply_to_message_id
        for message in messages
        if (message.reply_to_message_id or "").strip()
    }
    if not reply_ids:
        return {}
    rows = list((await db.scalars(select(Message).where(Message.id.in_(reply_ids)))).all())
    return {message.id: message for message in rows}


async def _resolve_reply_target(
    db: AsyncSession,
    *,
    chat_id: str,
    reply_to_message_id: str | None,
) -> Message | None:
    reply_id = (reply_to_message_id or "").strip()
    if not reply_id:
        return None
    reply_target = await db.scalar(
        select(Message).where(
            Message.id == reply_id,
            Message.chat_id == chat_id,
        )
    )
    if reply_target is None:
        raise HTTPException(status_code=404, detail="Reply target message not found.")
    return reply_target


async def list_conversations(
    db: AsyncSession,
    user_id: str,
    limit: int,
    public_base_url: str | None = None,
) -> list[ChatConversationOut]:
    last_message_subquery = (
        select(
            Message.chat_id.label("chat_id"),
            func.max(Message.created_at).label("last_message_at"),
        )
        .group_by(Message.chat_id)
        .subquery()
    )

    stmt = (
        select(ChatMember, Chat, last_message_subquery.c.last_message_at)
        .join(Chat, Chat.id == ChatMember.chat_id)
        .join(last_message_subquery, last_message_subquery.c.chat_id == Chat.id)
        .where(ChatMember.user_id == user_id)
        .order_by(desc(last_message_subquery.c.last_message_at), desc(Chat.created_at))
        .limit(limit)
    )

    rows = (await db.execute(stmt)).all()
    chat_ids = [chat.id for _member, chat, _last_message_at in rows]
    if not chat_ids:
        return []

    peer_rows = (
        await db.execute(
            select(ChatMember.chat_id, User)
            .join(User, ChatMember.user_id == User.id)
            .where(
                ChatMember.chat_id.in_(chat_ids),
                ChatMember.user_id != user_id,
            )
            .order_by(ChatMember.chat_id.asc(), ChatMember.joined_at.asc(), User.id.asc())
        )
    ).all()
    peers_by_chat_id: dict[str, User] = {}
    for chat_id, peer in peer_rows:
        peers_by_chat_id.setdefault(chat_id, peer)

    latest_message_ranked = (
        select(
            Message.chat_id.label("chat_id"),
            Message.sender_id.label("sender_id"),
            Message.body.label("body"),
            Message.message_type.label("message_type"),
            Message.deleted_at.label("deleted_at"),
            Message.updated_at.label("updated_at"),
            Message.created_at.label("created_at"),
            func.row_number()
            .over(
                partition_by=Message.chat_id,
                order_by=(Message.created_at.desc(), Message.id.desc()),
            )
            .label("message_rank"),
        )
        .where(Message.chat_id.in_(chat_ids))
        .subquery()
    )
    latest_message_rows = (
        await db.execute(
            select(
                latest_message_ranked.c.chat_id,
                latest_message_ranked.c.sender_id,
                latest_message_ranked.c.body,
                latest_message_ranked.c.message_type,
                latest_message_ranked.c.deleted_at,
                latest_message_ranked.c.updated_at,
                latest_message_ranked.c.created_at,
            ).where(latest_message_ranked.c.message_rank == 1)
        )
    ).all()
    latest_messages_by_chat_id = {
        chat_id: {
            "sender_id": sender_id,
            "body": body,
            "message_type": message_type,
            "deleted_at": deleted_at,
            "updated_at": updated_at,
            "created_at": created_at,
        }
        for chat_id, sender_id, body, message_type, deleted_at, updated_at, created_at in latest_message_rows
    }

    conversations: list[ChatConversationOut] = []
    for member, chat, last_message_at in rows:
        peer = peers_by_chat_id.get(chat.id)
        if peer is None:
            continue
        last_message = latest_messages_by_chat_id.get(chat.id)

        conversations.append(
            ChatConversationOut(
                chat_id=chat.id,
                peer=FeedAuthor(
                    id=peer.id,
                    display_name=peer.display_name,
                    avatar_url=_public_media_url(peer.avatar_url, public_base_url),
                    membership_tier=_daily_rewards.effective_membership_tier_user(peer),
                    is_pro=_daily_rewards.is_effective_pro_user(peer),
                ),
                last_message_text=(
                    _message_preview_from_fields(
                        body=str(last_message.get("body") or ""),
                        message_type=str(last_message.get("message_type") or "text"),
                        deleted_at=last_message.get("deleted_at"),
                    )
                    if last_message is not None
                    else ""
                ),
                last_message_sender_uid=(
                    str(last_message.get("sender_id") or "") or None
                    if last_message is not None
                    else None
                ),
                last_message_at=last_message_at,
                unread_count=member.unread_count,
                updated_at=(
                    last_message.get("updated_at")
                    if last_message is not None and last_message.get("updated_at") is not None
                    else last_message_at
                )
                or chat.created_at,
            )
        )

    return conversations


async def get_conversation_by_peer(
    db: AsyncSession,
    user_id: str,
    peer_id: str,
    public_base_url: str | None = None,
) -> ChatConversationOut | None:
    chat_id = await _find_direct_chat_id(db=db, user_id=user_id, peer_id=peer_id)
    if chat_id is None:
        return None

    conversations = await list_conversations(
        db=db,
        user_id=user_id,
        limit=200,
        public_base_url=public_base_url,
    )
    for conversation in conversations:
        if conversation.chat_id == chat_id:
            return conversation
    return None


async def list_messages(
    db: AsyncSession,
    chat_id: str,
    limit: int,
    cursor: str | None,
    public_base_url: str | None = None,
) -> ChatMessagesPage:
    stmt = (
        select(Message)
        .where(Message.chat_id == chat_id)
        .order_by(Message.created_at.desc(), Message.id.desc())
        .limit(limit + 1)
    )
    cursor_data = _decode_cursor(cursor)
    if cursor_data is not None:
        cursor_created_at, cursor_message_id = cursor_data
        stmt = stmt.where(
            or_(
                Message.created_at < cursor_created_at,
                and_(Message.created_at == cursor_created_at, Message.id < cursor_message_id),
            )
        )

    messages = list((await db.scalars(stmt)).all())
    has_more = len(messages) > limit
    visible = messages[:limit]
    reply_lookup = await _load_reply_lookup(db, visible)
    next_cursor = None
    if has_more and visible:
        last = visible[-1]
        next_cursor = _encode_cursor(last.created_at, last.id)

    return ChatMessagesPage(
        items=[
            _serialize_chat_message(
                message,
                recipient_id=None,
                reply_lookup=reply_lookup,
                public_base_url=public_base_url,
            )
            for message in reversed(visible)
        ],
        next_cursor=next_cursor,
        has_more=has_more,
    )


async def list_messages_with_peer(
    db: AsyncSession,
    user_id: str,
    peer_id: str,
    limit: int,
    cursor: str | None,
    *,
    create_chat_if_missing: bool = False,
    public_base_url: str | None = None,
) -> ChatMessagesPage:
    if create_chat_if_missing:
        chat_id = await _get_or_create_direct_chat_id(
            db=db,
            user_id=user_id,
            peer_id=peer_id,
        )
    else:
        chat_id = await _find_direct_chat_id(db=db, user_id=user_id, peer_id=peer_id)
        if chat_id is None:
            return ChatMessagesPage(
                items=[],
                next_cursor=None,
                has_more=False,
            )
    page = await list_messages(
        db=db,
        chat_id=chat_id,
        limit=limit,
        cursor=cursor,
        public_base_url=public_base_url,
    )
    return ChatMessagesPage(
        items=[
            item.model_copy(
                update={
                    "recipient_id": user_id if item.sender_id == peer_id else peer_id,
                }
            )
            for item in page.items
        ],
        next_cursor=page.next_cursor,
        has_more=page.has_more,
    )


async def send_message(
    db: AsyncSession,
    cache: RedisCache,
    bus: RedisEventBus,
    chat_id: str,
    sender_id: str,
    body: str,
    *,
    message_type: str = "text",
    media_url: str | None = None,
    media_duration_ms: int = 0,
    waveform: list[float] | None = None,
    reply_to_message_id: str | None = None,
    notification_service: NotificationService | None = None,
    public_base_url: str | None = None,
    connection_manager=None,
) -> ChatMessageOut:
    await ensure_user_exists(db, sender_id)
    normalized_body = body.strip()
    normalized_media_url = _normalize_message_media_url(media_url)
    normalized_message_type = _normalize_message_type(
        message_type=message_type,
        media_url=normalized_media_url,
        media_duration_ms=media_duration_ms,
    )
    if normalized_message_type in {"image", "voice"} and not normalized_media_url:
        raise HTTPException(status_code=422, detail="mediaUrl is required for media messages.")
    reply_target = await _resolve_reply_target(
        db,
        chat_id=chat_id,
        reply_to_message_id=reply_to_message_id,
    )
    message = Message(
        chat_id=chat_id,
        sender_id=sender_id,
        body=normalized_body,
        message_type=normalized_message_type,
        reply_to_message_id=reply_target.id if reply_target is not None else None,
        media_url=normalized_media_url,
        media_duration_ms=max(0, media_duration_ms),
        waveform_json=[float(item) for item in (waveform or [])[:128]],
    )
    db.add(message)
    await db.flush()

    await db.execute(update(Chat).where(Chat.id == chat_id).values(last_message_id=message.id))

    recipients = list(
        (
            await db.scalars(
                select(ChatMember.user_id).where(
                    ChatMember.chat_id == chat_id,
                    ChatMember.user_id != sender_id,
                )
            )
        ).all()
    )
    sender = await db.get(User, sender_id)
    sender_profile = await db.get(CommunityProfile, sender_id)

    if recipients:
        await db.execute(
            update(ChatMember)
            .where(ChatMember.chat_id == chat_id, ChatMember.user_id.in_(recipients))
            .values(unread_count=ChatMember.unread_count + 1)
        )

    await db.commit()
    await db.refresh(message)

    await bus.publish(
        f"room:{chat_id}",
        WsEnvelope(
            type="chat.message",
            topic=f"room:{chat_id}",
            data={
                "chat_id": chat_id,
                "message": _serialize_chat_message(
                    message,
                    recipient_id=recipients[0] if len(recipients) == 1 else None,
                    reply_lookup={reply_target.id: reply_target} if reply_target is not None else None,
                    public_base_url=public_base_url,
                ).model_dump(mode="json"),
            },
        ).model_dump(mode="json"),
    )

    for recipient_id in recipients:
        unread_total = await _compute_unread_total(db, recipient_id)
        await cache.set_unread_total(recipient_id, unread_total)
        await bus.publish(
            f"user:{recipient_id}",
            WsEnvelope(
                type="chat.unread_count",
                topic=f"user:{recipient_id}",
                data={"chat_id": chat_id, "unread_total": unread_total},
            ).model_dump(mode="json"),
        )
        should_notify = True
        if connection_manager is not None:
            should_notify = not connection_manager.is_user_in_room(recipient_id, chat_id)
        if notification_service is not None and should_notify:
            if normalized_message_type == "voice":
                preview = "Voice message"
            elif normalized_message_type == "image":
                preview = "Photo"
            else:
                preview = (
                    normalized_body
                    if len(normalized_body) <= 96
                    else f"{normalized_body[:96].rstrip()}..."
                )
            sender_display_name = (sender.display_name or "").strip() if sender is not None else ""
            sender_username = (sender.username or "").strip() if sender is not None else ""
            sender_avatar_source = (
                (sender_profile.avatar_url or "").strip()
                if sender_profile is not None and sender_profile.avatar_url
                else ((sender.avatar_url or "").strip() if sender is not None else "")
            )
            sender_avatar_url = _public_media_url(
                sender_avatar_source,
                public_base_url,
            ) or ""
            title = sender_display_name or sender_username or "New message"
            body = preview if not sender_username else f"@{sender_username} · {preview}"
            notification = await notification_service.create_notification(
                db,
                user_id=recipient_id,
                kind="direct_message",
                title=title,
                body=body,
                actor_uid=sender_id,
                post_id=None,
                extra_payload={
                    "peer_id": sender_id,
                    "message_id": message.id,
                    "message_type": normalized_message_type,
                    "sender_display_name": sender_display_name,
                    "sender_username": sender_username,
                    "sender_avatar_url": sender_avatar_url,
                },
            )
            await bus.publish(
                f"user:{recipient_id}",
                WsEnvelope(
                    type="notification.new",
                    topic=f"user:{recipient_id}",
                    data={
                        "notification": await notification_service.serialize_notification(
                            db,
                            notification,
                        )
                    },
                ).model_dump(mode="json"),
            )

    return _serialize_chat_message(
        message,
        recipient_id=recipients[0] if len(recipients) == 1 else None,
        reply_lookup={reply_target.id: reply_target} if reply_target is not None else None,
        public_base_url=public_base_url,
    )


async def send_message_to_peer(
    db: AsyncSession,
    cache: RedisCache,
    bus: RedisEventBus,
    peer_id: str,
    sender_id: str,
    body: str,
    *,
    message_type: str | None = "text",
    media_url: str | None = None,
    reply_to_message_id: str | None = None,
    notification_service: NotificationService | None = None,
    public_base_url: str | None = None,
    connection_manager=None,
) -> ChatMessageOut:
    chat_id = await _get_or_create_direct_chat_id(db=db, user_id=sender_id, peer_id=peer_id)
    return await send_message(
        db=db,
        cache=cache,
        bus=bus,
        chat_id=chat_id,
        sender_id=sender_id,
        body=body,
        message_type=message_type or "text",
        media_url=media_url,
        reply_to_message_id=reply_to_message_id,
        notification_service=notification_service,
        public_base_url=public_base_url,
        connection_manager=connection_manager,
    )


async def send_voice_message_to_peer(
    db: AsyncSession,
    cache: RedisCache,
    bus: RedisEventBus,
    *,
    peer_id: str,
    sender_id: str,
    media_url: str,
    duration_ms: int,
    waveform: list[float],
    reply_to_message_id: str | None = None,
    notification_service: NotificationService | None = None,
    public_base_url: str | None = None,
    connection_manager=None,
) -> ChatMessageOut:
    chat_id = await _get_or_create_direct_chat_id(db=db, user_id=sender_id, peer_id=peer_id)
    return await send_message(
        db=db,
        cache=cache,
        bus=bus,
        chat_id=chat_id,
        sender_id=sender_id,
        body="",
        message_type="voice",
        media_url=media_url,
        media_duration_ms=duration_ms,
        waveform=waveform,
        reply_to_message_id=reply_to_message_id,
        notification_service=notification_service,
        public_base_url=public_base_url,
        connection_manager=connection_manager,
    )


async def update_message_for_user(
    db: AsyncSession,
    bus: RedisEventBus,
    *,
    message_id: str,
    user_id: str,
    body: str,
    peer_id: str | None = None,
    public_base_url: str | None = None,
) -> ChatMessageOut:
    message, recipient_id = await _message_for_mutation(
        db=db,
        message_id=message_id,
        user_id=user_id,
        peer_id=peer_id,
    )
    if message.deleted_at is not None:
        raise HTTPException(status_code=409, detail="Message is already deleted.")

    message.body = body.strip()
    message.updated_at = datetime.utcnow()
    await db.commit()
    await db.refresh(message)

    serialized = _serialize_chat_message(
        message,
        recipient_id=recipient_id,
        reply_lookup=await _load_reply_lookup(db, [message]),
        public_base_url=public_base_url,
    )
    await bus.publish(
        f"room:{message.chat_id}",
        WsEnvelope(
            type="chat.message_updated",
            topic=f"room:{message.chat_id}",
            data={
                "chat_id": message.chat_id,
                "message": serialized.model_dump(mode="json"),
            },
        ).model_dump(mode="json"),
    )
    return serialized


async def delete_message_for_user(
    db: AsyncSession,
    bus: RedisEventBus,
    *,
    message_id: str,
    user_id: str,
    peer_id: str | None = None,
    public_base_url: str | None = None,
) -> None:
    message, recipient_id = await _message_for_mutation(
        db=db,
        message_id=message_id,
        user_id=user_id,
        peer_id=peer_id,
    )
    if message.deleted_at is not None:
        return

    message.body = ""
    message.media_url = None
    message.media_duration_ms = 0
    message.waveform_json = []
    message.updated_at = datetime.utcnow()
    message.deleted_at = message.updated_at
    await db.commit()
    await db.refresh(message)

    serialized = _serialize_chat_message(
        message,
        recipient_id=recipient_id,
        reply_lookup=await _load_reply_lookup(db, [message]),
        public_base_url=public_base_url,
    )
    await bus.publish(
        f"room:{message.chat_id}",
        WsEnvelope(
            type="chat.message_deleted",
            topic=f"room:{message.chat_id}",
            data={
                "chat_id": message.chat_id,
                "message": serialized.model_dump(mode="json"),
            },
        ).model_dump(mode="json"),
    )


async def delete_direct_chat_for_user(
    db: AsyncSession,
    cache: RedisCache,
    bus: RedisEventBus,
    *,
    user_id: str,
    peer_id: str,
) -> None:
    chat_id = await _find_direct_chat_id(db=db, user_id=user_id, peer_id=peer_id)
    if chat_id is None:
        raise HTTPException(status_code=404, detail="Chat not found.")

    member_ids = list(
        (
            await db.scalars(
                select(ChatMember.user_id).where(ChatMember.chat_id == chat_id)
            )
        ).all()
    )
    if user_id not in member_ids or peer_id not in member_ids:
        raise HTTPException(status_code=404, detail="Chat not found.")

    messages = list(
        (
            await db.scalars(
                select(Message)
                .where(Message.chat_id == chat_id)
                .order_by(Message.created_at.asc(), Message.id.asc())
            )
        ).all()
    )
    media_urls = {
        message.media_url.strip()
        for message in messages
        if (message.media_url or "").strip()
    }

    await db.execute(
        update(Chat).where(Chat.id == chat_id).values(last_message_id=None)
    )
    await db.execute(
        update(ChatMember)
        .where(ChatMember.chat_id == chat_id)
        .values(last_read_message_id=None, unread_count=0)
    )
    await db.execute(
        update(Message).where(Message.chat_id == chat_id).values(reply_to_message_id=None)
    )
    await db.execute(delete(ChatMember).where(ChatMember.chat_id == chat_id))
    await db.execute(delete(Message).where(Message.chat_id == chat_id))
    await db.execute(delete(Chat).where(Chat.id == chat_id))
    await db.commit()

    for media_url in media_urls:
        _delete_chat_media_file(media_url)

    await bus.publish(
        f"room:{chat_id}",
        WsEnvelope(
            type="chat.message_deleted",
            topic=f"room:{chat_id}",
            data={"chat_id": chat_id},
        ).model_dump(mode="json"),
    )

    for member_id in member_ids:
        unread_total = await _compute_unread_total(db, member_id)
        await cache.set_unread_total(member_id, unread_total)
        await bus.publish(
            f"user:{member_id}",
            WsEnvelope(
                type="chat.unread_count",
                topic=f"user:{member_id}",
                data={"chat_id": chat_id, "unread_total": unread_total},
            ).model_dump(mode="json"),
        )


async def mark_chat_read(
    db: AsyncSession,
    cache: RedisCache,
    bus: RedisEventBus,
    chat_id: str,
    user_id: str,
) -> None:
    latest_message_id = await db.scalar(
        select(Message.id)
        .where(Message.chat_id == chat_id)
        .order_by(Message.created_at.desc(), Message.id.desc())
        .limit(1)
    )
    await db.execute(
        update(ChatMember)
        .where(ChatMember.chat_id == chat_id, ChatMember.user_id == user_id)
        .values(last_read_message_id=latest_message_id, unread_count=0)
    )
    await db.commit()

    unread_total = await _compute_unread_total(db, user_id)
    await cache.set_unread_total(user_id, unread_total)
    await bus.publish(
        f"user:{user_id}",
        WsEnvelope(
            type="chat.unread_count",
            topic=f"user:{user_id}",
            data={"chat_id": chat_id, "unread_total": unread_total},
        ).model_dump(mode="json"),
    )


async def mark_chat_read_with_peer(
    db: AsyncSession,
    cache: RedisCache,
    bus: RedisEventBus,
    user_id: str,
    peer_id: str,
) -> None:
    chat_id = await _find_direct_chat_id(db=db, user_id=user_id, peer_id=peer_id)
    if chat_id is None:
        return
    await mark_chat_read(db=db, cache=cache, bus=bus, chat_id=chat_id, user_id=user_id)


async def mark_all_chats_read(
    db: AsyncSession,
    cache: RedisCache,
    bus: RedisEventBus,
    user_id: str,
) -> None:
    members = list(
        (
            await db.scalars(
                select(ChatMember).where(
                    ChatMember.user_id == user_id,
                    ChatMember.unread_count > 0,
                )
            )
        ).all()
    )
    if not members:
        await cache.set_unread_total(user_id, 0)
        return

    for member in members:
        latest_message_id = await db.scalar(
            select(Message.id)
            .where(Message.chat_id == member.chat_id)
            .order_by(Message.created_at.desc(), Message.id.desc())
            .limit(1)
        )
        member.last_read_message_id = latest_message_id
        member.unread_count = 0

    await db.commit()

    unread_total = await _compute_unread_total(db, user_id)
    await cache.set_unread_total(user_id, unread_total)
    for member in members:
        await bus.publish(
            f"user:{user_id}",
            WsEnvelope(
                type="chat.unread_count",
                topic=f"user:{user_id}",
                data={"chat_id": member.chat_id, "unread_total": unread_total},
            ).model_dump(mode="json"),
        )


async def get_unread_total(
    db: AsyncSession,
    cache: RedisCache,
    user_id: str,
) -> int:
    cached = await cache.get_unread_total(user_id)
    if cached is not None:
        return cached
    unread_total = await _compute_unread_total(db, user_id)
    await cache.set_unread_total(user_id, unread_total)
    return unread_total


async def _compute_unread_total(db: AsyncSession, user_id: str) -> int:
    value = await db.scalar(
        select(func.coalesce(func.sum(ChatMember.unread_count), 0)).where(ChatMember.user_id == user_id)
    )
    return int(value or 0)


async def _find_direct_chat_id(
    db: AsyncSession,
    user_id: str,
    peer_id: str,
) -> str | None:
    normalized_user_id = user_id.strip()
    normalized_peer_id = peer_id.strip()
    if (
        not normalized_user_id
        or not normalized_peer_id
        or normalized_user_id == normalized_peer_id
    ):
        return None
    candidate_ids = (
        select(ChatMember.chat_id)
        .where(ChatMember.user_id.in_([normalized_user_id, normalized_peer_id]))
        .group_by(ChatMember.chat_id)
        .having(func.count(func.distinct(ChatMember.user_id)) == 2)
        .subquery()
    )
    return await db.scalar(
        select(Chat.id)
        .where(Chat.id.in_(select(candidate_ids.c.chat_id)), Chat.chat_type == "direct")
        .limit(1)
    )


async def _get_or_create_direct_chat_id(
    db: AsyncSession,
    user_id: str,
    peer_id: str,
) -> str:
    normalized_user_id = user_id.strip()
    normalized_peer_id = peer_id.strip()
    if not normalized_user_id or not normalized_peer_id:
        raise HTTPException(status_code=422, detail="Both user ids are required.")
    if normalized_user_id == normalized_peer_id:
        raise HTTPException(
            status_code=400,
            detail="You cannot create a direct chat with yourself.",
        )

    await ensure_user_exists(db, normalized_user_id)
    await ensure_user_exists(db, normalized_peer_id)
    existing = await _find_direct_chat_id(
        db=db,
        user_id=normalized_user_id,
        peer_id=normalized_peer_id,
    )
    if existing is not None:
        return existing

    chat = Chat(chat_type="direct")
    db.add(chat)
    await db.flush()
    db.add_all(
        [
            ChatMember(chat_id=chat.id, user_id=normalized_user_id),
            ChatMember(chat_id=chat.id, user_id=normalized_peer_id),
        ]
    )
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        existing = await _find_direct_chat_id(
            db=db,
            user_id=normalized_user_id,
            peer_id=normalized_peer_id,
        )
        if existing is not None:
            return existing
        raise
    return chat.id


async def _message_for_mutation(
    db: AsyncSession,
    *,
    message_id: str,
    user_id: str,
    peer_id: str | None = None,
) -> tuple[Message, str | None]:
    message = await db.get(Message, message_id)
    if message is None:
        raise HTTPException(status_code=404, detail="Message not found.")
    if message.sender_id != user_id:
        raise HTTPException(status_code=403, detail="You can only modify your own message.")

    membership = await db.scalar(
        select(ChatMember.id).where(
            ChatMember.chat_id == message.chat_id,
            ChatMember.user_id == user_id,
        )
    )
    if membership is None:
        raise HTTPException(status_code=404, detail="Chat not found.")

    recipients = list(
        (
            await db.scalars(
                select(ChatMember.user_id).where(
                    ChatMember.chat_id == message.chat_id,
                    ChatMember.user_id != user_id,
                )
            )
        ).all()
    )
    recipient_id = recipients[0] if len(recipients) == 1 else None
    if peer_id is not None and recipient_id != peer_id:
        raise HTTPException(status_code=404, detail="Chat message not found for peer.")
    return message, recipient_id


def _public_media_url(value: str | None, public_base_url: str | None) -> str | None:
    raw = (value or "").strip()
    if not raw:
        return None
    base = (public_base_url or "").strip().rstrip("/")
    if raw.startswith("/media/"):
        return f"{base}{raw}" if base else raw
    marker = raw.find("/media/")
    if marker >= 0:
        relative = raw[marker:]
        return f"{base}{relative}" if base else relative
    return raw


def _normalize_message_media_url(value: str | None) -> str | None:
    raw = (value or "").strip()
    if not raw:
        return None
    if raw.startswith("/media/"):
        return raw
    marker = raw.find("/media/")
    if marker >= 0:
        return raw[marker:]
    return raw


def _normalize_message_type(
    *,
    message_type: str | None,
    media_url: str | None,
    media_duration_ms: int,
) -> str:
    normalized = (message_type or "").strip().lower()
    if normalized == "voice":
        return "voice"
    if normalized == "image":
        return "image"
    if media_url:
        return "voice" if media_duration_ms > 0 else "image"
    return "text"


def _delete_chat_media_file(raw_media_url: str) -> None:
    media_url = _normalize_message_media_url(raw_media_url)
    if media_url is None or not media_url.startswith("/media/"):
        return
    relative = media_url.removeprefix("/media/").strip("/")
    if not relative:
        return
    target_path = (_MEDIA_ROOT / relative).resolve()
    try:
        target_path.relative_to(_MEDIA_ROOT.resolve())
    except ValueError:
        return
    try:
        if target_path.exists() and target_path.is_file():
            target_path.unlink()
    except OSError:
        return

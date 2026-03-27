from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.entities import Notification, Post, PostReaction
from app.schemas.community import PostReactionStateOut
from app.schemas.ws import WsEnvelope
from app.services.cache import RedisCache
from app.services.community_support import (
    empty_reaction_counts,
    normalize_reaction_code,
    normalize_reaction_key,
    reaction_code_to_storage,
)
from app.services.notification_service import NotificationService
from app.services.user_service import ensure_user_exists
from app.ws.bus import RedisEventBus

logger = logging.getLogger(__name__)


async def react_to_post(
    db: AsyncSession,
    cache: RedisCache,
    bus: RedisEventBus,
    post_id: str,
    user_id: str,
    reaction_code: object,
    notification_service: NotificationService | None = None,
) -> PostReactionStateOut:
    await ensure_user_exists(db, user_id)
    post_author_id = await db.scalar(
        select(Post.author_id).where(Post.id == post_id).limit(1)
    )
    if post_author_id is None:
        raise ValueError("Post not found.")

    normalized_code = normalize_reaction_code(reaction_code)
    if normalized_code is None:
        raise ValueError("Reaction type is required.")
    storage_value = reaction_code_to_storage(normalized_code)
    if storage_value is None:
        raise ValueError("Reaction type is required.")
    reaction_key = normalize_reaction_key(normalized_code)

    existing = await db.scalar(
        select(PostReaction).where(
            PostReaction.post_id == post_id,
            PostReaction.user_id == user_id,
        )
    )

    existing_code = normalize_reaction_code(existing.reaction_type) if existing is not None else None

    next_reaction_code: int | None = normalized_code
    if existing is not None and existing_code == normalized_code:
        await db.delete(existing)
        next_reaction_code = None
        await cache.bump_post_reaction_count(post_id, str(normalized_code), -1)
    else:
        if existing is not None:
            if existing_code is not None:
                await cache.bump_post_reaction_count(post_id, str(existing_code), -1)
            existing.reaction_type = storage_value
        else:
            db.add(PostReaction(post_id=post_id, user_id=user_id, reaction_type=storage_value))
        await cache.bump_post_reaction_count(post_id, str(normalized_code), 1)

    await db.commit()

    counts = await cache.get_post_reaction_counts(post_id)
    if counts is None:
        counts = await _load_counts(db, post_id)
        await cache.set_post_reaction_counts(post_id, counts)
    normalized_counts = _normalize_cached_counts(counts)

    updated_at = datetime.now(timezone.utc)
    next_reaction_key = normalize_reaction_key(next_reaction_code)
    state = PostReactionStateOut(
        postId=post_id,
        authorUid=post_author_id,
        reaction=next_reaction_key,
        reactionKey=next_reaction_key,
        reactionIndex=next_reaction_code,
        reactionCounts=normalized_counts,
        updatedAt=updated_at,
    )

    _publish_reaction_change_async(bus=bus, state=state)

    if post_author_id != user_id and next_reaction_code is not None:
        title = "New reaction"
        body = f"Your post received a {reaction_key} reaction."
        if notification_service is not None:
            notification_service.queue_notification(
                user_id=post_author_id,
                kind="post.reaction",
                title=title,
                body=body,
                actor_uid=user_id,
                post_id=post_id,
                extra_payload={
                    "reaction_code": next_reaction_code,
                    "reaction_type": next_reaction_key,
                    "reaction_key": next_reaction_key,
                },
            )
        else:
            notification = Notification(
                user_id=post_author_id,
                event_type="post.reaction",
                payload_json={
                    "title": title,
                    "body": body,
                    "post_id": post_id,
                    "actor_uid": user_id,
                    "reaction_code": next_reaction_code,
                    "reaction_type": next_reaction_key,
                    "reaction_key": next_reaction_key,
                },
            )
            db.add(notification)
            await db.flush()
            await db.commit()
            await db.refresh(notification)
            await bus.publish(
                f"user:{post_author_id}",
                WsEnvelope(
                    type="notification.new",
                    topic=f"user:{post_author_id}",
                    data={
                        "notification": {
                            "id": notification.id,
                            "event_type": notification.event_type,
                            "payload": notification.payload_json,
                            "title": title,
                            "body": body,
                            "created_at": notification.created_at.isoformat(),
                        }
                    },
                ).model_dump(mode="json"),
            )

    return state


def _publish_reaction_change_async(
    *,
    bus: RedisEventBus,
    state: PostReactionStateOut,
) -> None:
    payload = WsEnvelope(
        type="post.reaction_changed",
        topic="feed:global",
        data=state.model_dump(mode="json"),
    ).model_dump(mode="json")

    async def publish() -> None:
        try:
            await bus.publish("feed:global", payload)
        except Exception:
            logger.exception(
                "post_reaction_publish_failed",
                extra={"postId": state.postId},
            )

    asyncio.create_task(publish())


async def _load_counts(db: AsyncSession, post_id: str) -> dict[str, int]:
    rows = await db.execute(
        select(PostReaction.reaction_type, func.count(PostReaction.id))
        .where(PostReaction.post_id == post_id)
        .group_by(PostReaction.reaction_type)
    )
    return {
        str(normalize_reaction_code(reaction_type)): int(count)
        for reaction_type, count in rows.all()
        if normalize_reaction_code(reaction_type) is not None
    }


def _normalize_cached_counts(raw: dict[str, int]) -> dict[int, int]:
    counts = empty_reaction_counts()
    for key, value in raw.items():
        code = normalize_reaction_code(key)
        if code is None:
            continue
        counts[code] = max(0, int(value))
    return counts

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.entities import Notification, Post, PostReaction
from app.schemas.community import PostReactionStateOut
from app.schemas.ws import WsEnvelope
from app.services.cache import RedisCache
from app.services.notification_service import NotificationService
from app.services.user_service import ensure_user_exists
from app.ws.bus import RedisEventBus


async def react_to_post(
    db: AsyncSession,
    cache: RedisCache,
    bus: RedisEventBus,
    post_id: str,
    user_id: str,
    reaction_type: str,
    notification_service: NotificationService | None = None,
) -> PostReactionStateOut:
    await ensure_user_exists(db, user_id)
    post = await db.get(Post, post_id)
    if post is None:
        raise ValueError("Post not found.")

    reaction_type = reaction_type.strip().lower()
    if not reaction_type:
        raise ValueError("Reaction type is required.")

    existing = await db.scalar(
        select(PostReaction).where(
            PostReaction.post_id == post_id,
            PostReaction.user_id == user_id,
        )
    )

    next_reaction: str | None = reaction_type
    if existing is not None and existing.reaction_type == reaction_type:
        await db.delete(existing)
        next_reaction = None
        await cache.bump_post_reaction_count(post_id, reaction_type, -1)
    else:
        if existing is not None:
            await cache.bump_post_reaction_count(post_id, existing.reaction_type, -1)
            existing.reaction_type = reaction_type
        else:
            db.add(PostReaction(post_id=post_id, user_id=user_id, reaction_type=reaction_type))
        await cache.bump_post_reaction_count(post_id, reaction_type, 1)

    await db.commit()

    counts = await cache.get_post_reaction_counts(post_id)
    if counts is None:
        counts = await _load_counts(db, post_id)
        await cache.set_post_reaction_counts(post_id, counts)

    updated_at = datetime.now(timezone.utc)
    state = PostReactionStateOut(
        postId=post_id,
        reaction=next_reaction,
        reactionCounts=counts,
        updatedAt=updated_at,
    )

    await bus.publish(
        "feed:global",
        WsEnvelope(
            type="post.reaction_changed",
            topic="feed:global",
            data=state.model_dump(mode="json"),
        ).model_dump(mode="json"),
    )

    if post.author_id != user_id and next_reaction is not None:
        title = "New reaction"
        body = f"Your post received a {next_reaction} reaction."
        if notification_service is not None:
            notification = await notification_service.create_notification(
                db,
                user_id=post.author_id,
                kind="post.reaction",
                title=title,
                body=body,
                actor_uid=user_id,
                post_id=post_id,
            )
        else:
            notification = Notification(
                user_id=post.author_id,
                event_type="post.reaction",
                payload_json={
                    "title": title,
                    "body": body,
                    "post_id": post_id,
                    "actor_uid": user_id,
                    "reaction_type": next_reaction,
                },
            )
            db.add(notification)
            await db.flush()
            await db.commit()
            await db.refresh(notification)
        await bus.publish(
            f"user:{post.author_id}",
            WsEnvelope(
                type="notification.new",
                topic=f"user:{post.author_id}",
                data={
                    "notification": (
                        await notification_service.serialize_notification(db, notification)
                        if notification_service is not None
                        else {
                            "id": notification.id,
                            "event_type": notification.event_type,
                            "payload": notification.payload_json,
                            "title": title,
                            "body": body,
                            "created_at": notification.created_at.isoformat(),
                        }
                    )
                },
            ).model_dump(mode="json"),
        )

    return state


async def _load_counts(db: AsyncSession, post_id: str) -> dict[str, int]:
    rows = await db.execute(
        select(PostReaction.reaction_type, func.count(PostReaction.id))
        .where(PostReaction.post_id == post_id)
        .group_by(PostReaction.reaction_type)
    )
    return {reaction_type: count for reaction_type, count in rows.all()}

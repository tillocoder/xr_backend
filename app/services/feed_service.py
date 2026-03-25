from __future__ import annotations

import base64
import json
from datetime import datetime

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.entities import CommunityFollow, Post, PostReaction, User
from app.schemas.feed import FeedAuthor, FeedItem, FeedPage, FeedPost, FeedViewerState
from app.services.cache import RedisCache
from app.services.daily_reward_service import DailyRewardService

_daily_rewards = DailyRewardService()


def _encode_cursor(created_at: datetime, post_id: str) -> str:
    payload = {"created_at": created_at.isoformat(), "post_id": post_id}
    return base64.urlsafe_b64encode(json.dumps(payload).encode("utf-8")).decode("utf-8")


def _decode_cursor(cursor: str | None) -> tuple[datetime, str] | None:
    if not cursor:
        return None
    data = json.loads(base64.urlsafe_b64decode(cursor.encode("utf-8")).decode("utf-8"))
    return datetime.fromisoformat(data["created_at"]), data["post_id"]


async def load_feed_page(
    db: AsyncSession,
    cache: RedisCache,
    viewer_id: str,
    cursor: str | None,
    limit: int,
) -> FeedPage:
    stmt = select(Post).order_by(Post.created_at.desc(), Post.id.desc()).limit(limit + 1)
    cursor_data = _decode_cursor(cursor)
    if cursor_data is not None:
        cursor_created_at, cursor_post_id = cursor_data
        stmt = stmt.where(
            or_(
                Post.created_at < cursor_created_at,
                and_(Post.created_at == cursor_created_at, Post.id < cursor_post_id),
            )
        )

    posts = list((await db.scalars(stmt)).all())
    has_more = len(posts) > limit
    visible_posts = posts[:limit]
    post_ids = [post.id for post in visible_posts]
    author_ids = sorted({post.author_id for post in visible_posts})

    authors_result = await db.execute(select(User).where(User.id.in_(author_ids or [""])))
    authors = {author.id: author for author in authors_result.scalars().all()}

    viewer_reactions_result = await db.execute(
        select(PostReaction.post_id, PostReaction.reaction_type).where(
            PostReaction.user_id == viewer_id,
            PostReaction.post_id.in_(post_ids or [""]),
        )
    )
    viewer_reactions = {post_id: reaction for post_id, reaction in viewer_reactions_result.all()}

    following_result = await db.execute(
        select(CommunityFollow.following_uid).where(
            CommunityFollow.follower_uid == viewer_id,
            CommunityFollow.following_uid.in_(author_ids or [""]),
        )
    )
    following_author_ids = {author_id for (author_id,) in following_result.all()}

    reaction_counts_by_post = await cache.get_post_reaction_counts_many(post_ids)
    missing_reaction_post_ids = [
        post_id for post_id in post_ids if post_id not in reaction_counts_by_post
    ]
    if missing_reaction_post_ids:
        loaded_reaction_counts = await _load_reaction_counts_map_from_db(db, missing_reaction_post_ids)
        reaction_counts_by_post.update(loaded_reaction_counts)
        for post_id in missing_reaction_post_ids:
            await cache.set_post_reaction_counts(
                post_id,
                loaded_reaction_counts.get(post_id, {}),
            )

    items: list[FeedItem] = []
    for post in visible_posts:
        author = authors.get(post.author_id)
        if author is None:
            continue
        reaction_counts = reaction_counts_by_post.get(post.id, {})

        items.append(
            FeedItem(
                post=FeedPost(
                    id=post.id,
                    content=post.content,
                    comment_count=post.comment_count,
                    reaction_counts=reaction_counts,
                    created_at=post.created_at,
                ),
                author=FeedAuthor(
                    id=author.id,
                    display_name=author.display_name,
                    avatar_url=author.avatar_url,
                    membership_tier=_daily_rewards.effective_membership_tier_user(author),
                    is_pro=_daily_rewards.is_effective_pro_user(author),
                ),
                viewer_state=FeedViewerState(
                    reaction=viewer_reactions.get(post.id),
                    is_following_author=post.author_id in following_author_ids,
                ),
            )
        )

    next_cursor = None
    if has_more and visible_posts:
        last_post = visible_posts[-1]
        next_cursor = _encode_cursor(last_post.created_at, last_post.id)

    return FeedPage(items=items, next_cursor=next_cursor, has_more=has_more)


async def _load_reaction_counts_from_db(db: AsyncSession, post_id: str) -> dict[str, int]:
    return (await _load_reaction_counts_map_from_db(db, [post_id])).get(post_id, {})


async def _load_reaction_counts_map_from_db(
    db: AsyncSession,
    post_ids: list[str],
) -> dict[str, dict[str, int]]:
    normalized_post_ids = [post_id for post_id in post_ids if post_id]
    if not normalized_post_ids:
        return {}
    result = await db.execute(
        select(PostReaction.post_id, PostReaction.reaction_type, func.count(PostReaction.id))
        .where(PostReaction.post_id.in_(normalized_post_ids))
        .group_by(PostReaction.post_id, PostReaction.reaction_type)
    )
    grouped = {post_id: {} for post_id in normalized_post_ids}
    for post_id, reaction_type, count in result.all():
        grouped.setdefault(post_id, {})[reaction_type] = count
    return grouped

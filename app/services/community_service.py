from __future__ import annotations

import base64
import binascii
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from fastapi import HTTPException, status
from sqlalchemy import case, delete, desc, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.entities import (
    Comment,
    CommentReaction,
    CommunityFollow,
    CommunityProfile,
    PollVote,
    Post,
    PostReaction,
    PostView,
    User,
)
from app.schemas.community import (
    CommunityAddCommentRequest,
    CommunityCommentResponse,
    CommunityCreatePostRequest,
    CommunityImageUploadRequest,
    CommunityImageUploadResponse,
    CommunityPollResponse,
    CommunityPostResponse,
    CommunityProfileResponse,
    CommunityProfileUpsertRequest,
    CommunityPublicHoldingSummaryResponse,
    CommunityUpdatePostRequest,
)
from app.schemas.ws import WsEnvelope
from app.services.community_support import (
    CommunityResponseFactory,
    empty_reaction_counts,
    fallback_username,
    holding_summaries_from_json,
    is_profile_username_conflict,
    non_negative_int,
    normalize_display_name,
    normalize_market_bias,
    normalize_media_reference,
    normalize_reaction_key,
    normalize_short_text,
    normalize_social_accounts,
    normalize_symbol,
    normalize_symbols,
    normalize_username,
    profile_score,
    public_media_url,
)
from app.services.daily_reward_service import DailyRewardService
from app.services.membership_tiers import MEMBERSHIP_TIER_LEGEND, MEMBERSHIP_TIER_PRO
from app.services.notification_service import NotificationService
from app.services.user_service import ensure_user_exists
from app.ws.bus import RedisEventBus

_COMMUNITY_POSTING_OVERRIDE_UID = "esLtkcFW2KfBWFNaWUWwpsbEpuI2"


class CommunityService:
    def __init__(
        self,
        *,
        notification_service: NotificationService | None = None,
        bus: RedisEventBus | None = None,
        public_base_url: str | None = None,
    ) -> None:
        self._notifications = notification_service
        self._bus = bus
        self._daily_rewards = DailyRewardService()
        self._media_root = Path(__file__).resolve().parents[2] / "media"
        self._public_base_url = (public_base_url or "").strip().rstrip("/")
        self._responses = CommunityResponseFactory(
            daily_rewards=self._daily_rewards,
            public_base_url=self._public_base_url,
        )

    async def list_posts(
        self,
        db: AsyncSession,
        *,
        symbol: str | None,
        limit: int,
    ) -> list[CommunityPostResponse]:
        normalized_limit = max(1, min(limit, 100))
        fetch_limit = normalized_limit if not symbol else max(36, min(normalized_limit * 3, 90))
        membership_priority = case(
            (User.membership_tier == MEMBERSHIP_TIER_LEGEND, 2),
            (
                (User.membership_tier == MEMBERSHIP_TIER_PRO) | (User.is_pro.is_(True)),
                1,
            ),
            else_=0,
        )
        posts = list(
            (
                await db.scalars(
                    select(Post)
                    .join(User, User.id == Post.author_id)
                    .order_by(
                        desc(membership_priority),
                        Post.created_at.desc(),
                        Post.id.desc(),
                    )
                    .limit(fetch_limit)
                )
            ).all()
        )
        normalized_symbol = normalize_symbol(symbol)
        if normalized_symbol is not None:
            posts = [
                post
                for post in posts
                if post.symbol == normalized_symbol
                or normalized_symbol in normalize_symbols(post.symbols_json)
            ]
        return await self._serialize_posts(db, posts[:normalized_limit])

    async def get_post(self, db: AsyncSession, post_id: str) -> CommunityPostResponse:
        post = await db.get(Post, post_id.strip())
        if post is None:
            raise self._not_found("Post not found.")
        rows = await self._serialize_posts(db, [post])
        if not rows:
            raise self._not_found("Post not found.")
        return rows[0]

    async def list_posts_by_author(
        self,
        db: AsyncSession,
        *,
        author_uid: str,
        limit: int,
    ) -> list[CommunityPostResponse]:
        normalized_uid = author_uid.strip()
        if not normalized_uid:
            return []
        posts = list(
            (
                await db.scalars(
                    select(Post)
                    .where(Post.author_id == normalized_uid)
                    .order_by(Post.created_at.desc(), Post.id.desc())
                    .limit(max(1, min(limit, 100)))
                )
            ).all()
        )
        return await self._serialize_posts(db, posts)

    async def list_comments(
        self,
        db: AsyncSession,
        *,
        post_id: str,
        limit: int,
    ) -> list[CommunityCommentResponse]:
        normalized_post_id = post_id.strip()
        if not normalized_post_id:
            return []
        comments = list(
            (
                await db.scalars(
                    select(Comment)
                    .where(Comment.post_id == normalized_post_id)
                    .order_by(Comment.created_at.desc(), Comment.id.desc())
                    .limit(max(1, min(limit, 120)))
                )
            ).all()
        )
        return await self._serialize_comments(db, comments)

    async def list_comments_by_author(
        self,
        db: AsyncSession,
        *,
        author_uid: str,
        limit: int,
    ) -> list[CommunityCommentResponse]:
        normalized_uid = author_uid.strip()
        if not normalized_uid:
            return []
        comments = list(
            (
                await db.scalars(
                    select(Comment)
                    .where(Comment.author_id == normalized_uid)
                    .order_by(Comment.created_at.desc(), Comment.id.desc())
                    .limit(max(1, min(limit, 120)))
                )
            ).all()
        )
        return await self._serialize_comments(db, comments)

    async def get_reaction(self, db: AsyncSession, *, post_id: str, user_uid: str) -> str | None:
        normalized_post_id = post_id.strip()
        normalized_user_id = user_uid.strip()
        if not normalized_post_id or not normalized_user_id:
            return None
        reaction = await db.scalar(
            select(PostReaction.reaction_type).where(
                PostReaction.post_id == normalized_post_id,
                PostReaction.user_id == normalized_user_id,
            )
        )
        return normalize_reaction_key(reaction)

    async def get_profile(self, db: AsyncSession, uid: str) -> CommunityProfileResponse:
        user = await db.get(User, uid.strip())
        if user is None:
            raise self._not_found("Profile not found.")
        profile = await db.get(CommunityProfile, user.id)
        return self._responses.profile_response(user, profile)

    async def list_recent_profiles(
        self,
        db: AsyncSession,
        *,
        limit: int,
    ) -> list[CommunityProfileResponse]:
        users = list(
            (
                await db.scalars(
                    select(User)
                    .order_by(
                        desc(
                            case(
                                (User.membership_tier == MEMBERSHIP_TIER_LEGEND, 2),
                                (
                                    (User.membership_tier == MEMBERSHIP_TIER_PRO)
                                    | (User.is_pro.is_(True)),
                                    1,
                                ),
                                else_=0,
                            )
                        ),
                        desc(User.updated_at),
                        desc(User.created_at),
                    )
                    .limit(max(1, min(limit, 100)))
                )
            ).all()
        )
        return await self._serialize_profiles(db, users)

    async def search_profiles(
        self,
        db: AsyncSession,
        *,
        query: str,
        limit: int,
    ) -> list[CommunityProfileResponse]:
        normalized_query = query.strip().lower()
        users = list(
            (
                await db.scalars(
                    select(User)
                    .order_by(
                        desc(
                            case(
                                (User.membership_tier == MEMBERSHIP_TIER_LEGEND, 2),
                                (
                                    (User.membership_tier == MEMBERSHIP_TIER_PRO)
                                    | (User.is_pro.is_(True)),
                                    1,
                                ),
                                else_=0,
                            )
                        ),
                        desc(User.updated_at),
                        desc(User.created_at),
                    )
                    .limit(200)
                )
            ).all()
        )
        profiles = await self._serialize_profiles(db, users)
        if not normalized_query:
            return profiles[: max(1, min(limit, 50))]
        tokens = [token for token in normalized_query.split() if token]
        if not tokens:
            return profiles[: max(1, min(limit, 50))]
        filtered = [
            item
            for item in profiles
            if all(
                token in item.displayName.lower() or token in item.username.lower()
                for token in tokens
            )
        ]
        filtered.sort(key=lambda item: profile_score(item, tokens), reverse=True)
        return filtered[: max(1, min(limit, 50))]

    async def get_public_holding_summaries(
        self,
        db: AsyncSession,
        *,
        uid: str,
    ) -> dict[str, CommunityPublicHoldingSummaryResponse]:
        user = await db.get(User, uid.strip())
        if user is None:
            raise self._not_found("Profile not found.")
        summaries = holding_summaries_from_json(user.holdings_json)
        return {
            symbol: CommunityPublicHoldingSummaryResponse(
                amount=item["amount"],
                avgBuyPrice=item["avgBuyPrice"],
                entryDate=item["entryDate"],
            )
            for symbol, item in summaries.items()
        }

    async def get_follower_count(self, db: AsyncSession, *, uid: str) -> int:
        return await self._count_follows(db, column_name="following", uid=uid)

    async def get_following_count(self, db: AsyncSession, *, uid: str) -> int:
        return await self._count_follows(db, column_name="follower", uid=uid)

    async def list_followers(self, db: AsyncSession, *, uid: str) -> list[CommunityProfileResponse]:
        normalized_uid = uid.strip()
        if not normalized_uid:
            return []
        rows = list(
            (
                await db.scalars(
                    select(User)
                    .join(CommunityFollow, CommunityFollow.follower_uid == User.id)
                    .where(CommunityFollow.following_uid == normalized_uid)
                    .order_by(CommunityFollow.created_at.desc())
                )
            ).all()
        )
        return await self._serialize_profiles(db, rows)

    async def list_following(self, db: AsyncSession, *, uid: str) -> list[CommunityProfileResponse]:
        normalized_uid = uid.strip()
        if not normalized_uid:
            return []
        rows = list(
            (
                await db.scalars(
                    select(User)
                    .join(CommunityFollow, CommunityFollow.following_uid == User.id)
                    .where(CommunityFollow.follower_uid == normalized_uid)
                    .order_by(CommunityFollow.created_at.desc())
                )
            ).all()
        )
        return await self._serialize_profiles(db, rows)

    async def is_following(self, db: AsyncSession, *, viewer_uid: str, target_uid: str) -> bool:
        normalized_viewer_uid = viewer_uid.strip()
        normalized_target_uid = target_uid.strip()
        if (
            not normalized_viewer_uid
            or not normalized_target_uid
            or normalized_viewer_uid == normalized_target_uid
        ):
            return False
        row = await db.scalar(
            select(CommunityFollow.id).where(
                CommunityFollow.follower_uid == normalized_viewer_uid,
                CommunityFollow.following_uid == normalized_target_uid,
            )
        )
        return row is not None

    async def is_user_pro(self, db: AsyncSession, *, uid: str) -> bool:
        normalized_uid = uid.strip()
        if not normalized_uid:
            return False
        if normalized_uid == _COMMUNITY_POSTING_OVERRIDE_UID:
            return True
        return await self._daily_rewards.is_effective_pro(db, user_id=normalized_uid)

    async def sync_profile(
        self,
        db: AsyncSession,
        *,
        current_user_id: str,
        payload: CommunityProfileUpsertRequest,
    ) -> CommunityProfileResponse:
        return await self._upsert_profile(
            db,
            current_user_id=current_user_id,
            payload=payload,
            strict_username=False,
        )

    async def update_profile(
        self,
        db: AsyncSession,
        *,
        current_user_id: str,
        payload: CommunityProfileUpsertRequest,
    ) -> CommunityProfileResponse:
        return await self._upsert_profile(
            db,
            current_user_id=current_user_id,
            payload=payload,
            strict_username=True,
        )

    async def follow(
        self,
        db: AsyncSession,
        *,
        current_user_id: str,
        target_uid: str,
    ) -> None:
        normalized_target_uid = target_uid.strip()
        if not normalized_target_uid or normalized_target_uid == current_user_id.strip():
            return
        actor = await ensure_user_exists(db, current_user_id)
        target = await db.get(User, normalized_target_uid)
        if target is None:
            raise self._not_found("Profile not found.")

        existing = await db.scalar(
            select(CommunityFollow.id).where(
                CommunityFollow.follower_uid == actor.id,
                CommunityFollow.following_uid == normalized_target_uid,
            )
        )
        if existing is not None:
            return

        db.add(CommunityFollow(follower_uid=actor.id, following_uid=normalized_target_uid))
        await db.commit()

        if self._notifications is not None:
            notification = await self._notifications.create_notification(
                db,
                user_id=normalized_target_uid,
                kind="community_follow",
                title="New follower",
                body=f"{normalize_display_name(actor.display_name) or 'XR HODL Member'} followed you.",
                actor_uid=actor.id,
                post_id=None,
            )
            await self._publish_notification(db, normalized_target_uid, notification)

    async def unfollow(
        self,
        db: AsyncSession,
        *,
        current_user_id: str,
        target_uid: str,
    ) -> None:
        normalized_target_uid = target_uid.strip()
        normalized_current_user_id = current_user_id.strip()
        if not normalized_target_uid or not normalized_current_user_id:
            return
        await db.execute(
            delete(CommunityFollow).where(
                CommunityFollow.follower_uid == normalized_current_user_id,
                CommunityFollow.following_uid == normalized_target_uid,
            )
        )
        await db.commit()

    async def create_post(
        self,
        db: AsyncSession,
        *,
        current_user_id: str,
        payload: CommunityCreatePostRequest,
    ) -> CommunityPostResponse:
        user = await ensure_user_exists(db, current_user_id)
        await self._ensure_profile(db, user)
        now = self._now()

        has_override = user.id == _COMMUNITY_POSTING_OVERRIDE_UID
        is_effective_pro = has_override or self._daily_rewards.is_effective_pro_user(user, now=now)
        if not is_effective_pro:
            raise self._forbidden("Collect daily rewards or activate Pro to publish community posts.")

        paid_membership_tier = self._daily_rewards.paid_membership_tier_user(user)
        if paid_membership_tier in {MEMBERSHIP_TIER_PRO, MEMBERSHIP_TIER_LEGEND}:
            posts_today = await self._daily_rewards.count_posts_for_reward_day(
                db,
                user_id=user.id,
                now=now,
            )
            if posts_today >= self._daily_rewards.paid_pro_posts_per_day:
                raise self._bad_request(
                    f"Paid Pro accounts can publish up to {self._daily_rewards.paid_pro_posts_per_day} posts per day."
                )
        elif self._daily_rewards.is_reward_limited_pro_user(user, now=now):
            posts_today = await self._daily_rewards.count_posts_for_reward_day(
                db,
                user_id=user.id,
                now=now,
            )
            if posts_today >= self._daily_rewards.reward_pro_posts_per_day:
                raise self._bad_request(
                    f"Reward Pro accounts can publish up to {self._daily_rewards.reward_pro_posts_per_day} posts per day."
                )

        content = normalize_short_text(payload.content, max_length=2000)
        if not content:
            raise self._bad_request("Post content cannot be empty.")
        symbols = normalize_symbols(([payload.symbol] if payload.symbol else []) + payload.symbols)
        poll_options = [
            normalized
            for normalized in (
                normalize_short_text(option, max_length=60)
                for option in payload.pollOptions[:4]
            )
            if normalized
        ]
        poll_duration_days = (
            max(1, min(int(payload.pollDurationDays or 0), 7))
            if len(poll_options) >= 2 and payload.pollDurationDays
            else None
        )
        post = Post(
            author_id=user.id,
            content=content,
            symbol=symbols[0] if symbols else None,
            symbols_json=symbols,
            image_url=normalize_media_reference(payload.imageUrl),
            market_bias=normalize_market_bias(payload.marketBias),
            poll_options_json=poll_options,
            poll_vote_counts_json=[0 for _ in poll_options],
            poll_vote_total=0,
            poll_duration_days=poll_duration_days,
            poll_ends_at=(now + timedelta(days=poll_duration_days)) if poll_duration_days else None,
            comment_count=0,
            view_count=0,
            updated_at=now,
        )
        db.add(post)
        await db.commit()
        await db.refresh(post)

        response = await self.get_post(db, post.id)
        await self._publish_feed_post_created(response)
        await self._notify_followers_of_new_post(db, actor=user, post=response)
        return response

    async def update_post(
        self,
        db: AsyncSession,
        *,
        current_user_id: str,
        post_id: str,
        payload: CommunityUpdatePostRequest,
    ) -> CommunityPostResponse:
        post = await db.get(Post, post_id.strip())
        if post is None:
            raise self._not_found("Post not found.")
        if post.author_id != current_user_id.strip():
            raise self._forbidden("You can only edit your own posts.")
        content = normalize_short_text(payload.content, max_length=2000)
        if not content:
            raise self._bad_request("Post content cannot be empty.")
        post.content = content
        post.updated_at = self._now()
        await db.commit()
        return await self.get_post(db, post.id)

    async def delete_post(
        self,
        db: AsyncSession,
        *,
        current_user_id: str,
        post_id: str,
    ) -> None:
        normalized_post_id = post_id.strip()
        post = await db.get(Post, normalized_post_id)
        if post is None:
            raise self._not_found("Post not found.")
        if post.author_id != current_user_id.strip():
            raise self._forbidden("You can only delete your own posts.")

        comment_ids = list((await db.scalars(select(Comment.id).where(Comment.post_id == normalized_post_id))).all())
        if comment_ids:
            await db.execute(update(Comment).where(Comment.id.in_(comment_ids)).values(reply_to_comment_id=None))
            await db.execute(delete(CommentReaction).where(CommentReaction.comment_id.in_(comment_ids)))
        await db.execute(delete(Comment).where(Comment.post_id == normalized_post_id))
        await db.execute(delete(PollVote).where(PollVote.post_id == normalized_post_id))
        await db.execute(delete(PostView).where(PostView.post_id == normalized_post_id))
        await db.execute(delete(PostReaction).where(PostReaction.post_id == normalized_post_id))
        await db.delete(post)
        await db.commit()

    async def vote_on_poll(
        self,
        db: AsyncSession,
        *,
        current_user_id: str,
        post_id: str,
        option_index: int,
    ) -> None:
        normalized_post_id = post_id.strip()
        if not normalized_post_id:
            raise self._bad_request("Poll vote could not be recorded.")
        await ensure_user_exists(db, current_user_id)
        post = await db.get(Post, normalized_post_id)
        if post is None:
            raise self._not_found("Post not found.")
        options = [item for item in post.poll_options_json if str(item).strip()]
        if len(options) < 2:
            raise self._bad_request("This post has no active poll.")
        if option_index < 0 or option_index >= len(options):
            raise self._bad_request("Invalid poll option.")
        if post.poll_ends_at is not None and post.poll_ends_at < self._now():
            raise self._bad_request("This poll has ended.")

        counts = [non_negative_int(item) for item in post.poll_vote_counts_json]
        while len(counts) < len(options):
            counts.append(0)

        existing = await db.scalar(
            select(PollVote).where(
                PollVote.post_id == normalized_post_id,
                PollVote.user_id == current_user_id.strip(),
            )
        )
        previous_index = existing.option_index if existing is not None else None
        if previous_index == option_index:
            return
        if previous_index is not None and 0 <= previous_index < len(counts) and counts[previous_index] > 0:
            counts[previous_index] -= 1
        counts[option_index] += 1

        stmt = insert(PollVote).values(
            post_id=normalized_post_id,
            user_id=current_user_id.strip(),
            option_index=option_index,
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=[PollVote.post_id, PollVote.user_id],
            set_={"option_index": option_index},
        )
        await db.execute(stmt)
        post.poll_vote_counts_json = counts
        post.poll_vote_total = sum(counts)
        post.updated_at = self._now()
        await db.commit()

    async def register_post_view(
        self,
        db: AsyncSession,
        *,
        current_user_id: str,
        post_id: str,
    ) -> None:
        normalized_post_id = post_id.strip()
        if not normalized_post_id:
            return
        await ensure_user_exists(db, current_user_id)
        post = await db.get(Post, normalized_post_id)
        if post is None:
            return
        existing = await db.scalar(
            select(PostView.id).where(
                PostView.post_id == normalized_post_id,
                PostView.user_id == current_user_id.strip(),
            )
        )
        if existing is not None:
            return
        db.add(PostView(post_id=normalized_post_id, user_id=current_user_id.strip()))
        post.view_count = non_negative_int(post.view_count) + 1
        post.updated_at = self._now()
        await db.commit()

    async def add_comment(
        self,
        db: AsyncSession,
        *,
        current_user_id: str,
        post_id: str,
        payload: CommunityAddCommentRequest,
    ) -> CommunityCommentResponse:
        normalized_post_id = post_id.strip()
        content = normalize_short_text(payload.content, max_length=1000)
        if not normalized_post_id or not content:
            raise self._bad_request("Comment cannot be empty.")
        user = await ensure_user_exists(db, current_user_id)
        await self._ensure_profile(db, user)
        post = await db.get(Post, normalized_post_id)
        if post is None:
            raise self._not_found("Post not found.")

        comment = Comment(
            post_id=normalized_post_id,
            author_id=user.id,
            reply_to_comment_id=(payload.replyToCommentId or "").strip() or None,
            reply_to_author_username=normalize_username(payload.replyToAuthorUsername or "") or None,
            content=content,
        )
        db.add(comment)
        post.comment_count = non_negative_int(post.comment_count) + 1
        post.updated_at = self._now()
        await db.commit()
        await db.refresh(comment)
        rows = await self._serialize_comments(db, [comment])
        return rows[0]

    async def react_to_comment(
        self,
        db: AsyncSession,
        *,
        current_user_id: str,
        post_id: str,
        comment_id: str,
        reaction_code: int,
    ) -> None:
        normalized_post_id = post_id.strip()
        normalized_comment_id = comment_id.strip()
        if not normalized_post_id or not normalized_comment_id:
            raise self._bad_request("Comment reaction could not be saved.")
        await ensure_user_exists(db, current_user_id)
        comment = await db.scalar(
            select(Comment).where(Comment.id == normalized_comment_id, Comment.post_id == normalized_post_id)
        )
        if comment is None:
            raise self._not_found("Comment not found.")
        existing = await db.scalar(
            select(CommentReaction).where(
                CommentReaction.comment_id == normalized_comment_id,
                CommentReaction.user_id == current_user_id.strip(),
            )
        )
        if existing is not None and existing.reaction_code == reaction_code:
            await db.delete(existing)
        else:
            stmt = insert(CommentReaction).values(
                comment_id=normalized_comment_id,
                user_id=current_user_id.strip(),
                reaction_code=reaction_code,
            )
            stmt = stmt.on_conflict_do_update(
                index_elements=[CommentReaction.comment_id, CommentReaction.user_id],
                set_={"reaction_code": reaction_code},
            )
            await db.execute(stmt)
        await db.commit()

    async def save_media_file(
        self,
        payload: CommunityImageUploadRequest,
        *,
        category: str,
    ) -> CommunityImageUploadResponse:
        file_name = Path(payload.fileName.strip() or "image.jpg").name
        suffix = Path(file_name).suffix or ".jpg"
        try:
            raw = base64.b64decode(payload.contentBase64, validate=True)
        except (ValueError, binascii.Error):
            raise self._bad_request("Invalid base64 image payload.")
        if not raw:
            raise self._bad_request("Empty image payload.")

        target_dir = self._media_root / category
        target_dir.mkdir(parents=True, exist_ok=True)
        stored_name = f"{uuid4().hex}{suffix.lower()[:8]}"
        target_path = target_dir / stored_name
        target_path.write_bytes(raw)

        relative_path = f"/media/{category.strip('/')}/{stored_name}"
        public_url = public_media_url(relative_path, self._public_base_url) or relative_path
        return CommunityImageUploadResponse(url=public_url, path=relative_path)

    async def _upsert_profile(
        self,
        db: AsyncSession,
        *,
        current_user_id: str,
        payload: CommunityProfileUpsertRequest,
        strict_username: bool,
    ) -> CommunityProfileResponse:
        user = await ensure_user_exists(db, current_user_id)
        profile = await self._ensure_profile(db, user)
        now = self._now()
        display_name = normalize_display_name(payload.displayName)
        if not display_name:
            raise self._bad_request("Display name cannot be empty.")
        username = normalize_username(payload.username)
        if len(username) < 3:
            raise self._bad_request("Username must be at least 3 characters.")

        claimed_username = await self._claim_username(
            db,
            uid=user.id,
            desired=username,
            strict=strict_username,
        )
        if profile.display_name != display_name:
            profile.display_name_change_count = non_negative_int(profile.display_name_change_count) + 1
            profile.display_name_window_started_at = now
        if profile.username != claimed_username:
            profile.username_updated_at = now

        profile.username = claimed_username
        profile.display_name = display_name
        profile.avatar_url = normalize_media_reference(payload.avatarUrl)
        profile.cover_image_url = normalize_media_reference(payload.coverImageUrl)
        profile.biography = normalize_short_text(payload.biography, max_length=160)
        profile.birthday_label = normalize_short_text(payload.birthdayLabel, max_length=24)
        profile.website = normalize_short_text(payload.website, max_length=80)
        profile.social_accounts_json = normalize_social_accounts(payload.socialAccounts)
        profile.public_watchlist_symbols_json = normalize_symbols(payload.publicWatchlistSymbols)
        profile.blocked_account_ids_json = sorted(
            {
                item.strip()
                for item in payload.blockedAccountIds
                if item.strip() and item.strip() != user.id
            }
        )
        profile.is_pro = self._daily_rewards.is_effective_pro_user(user, now=now)
        profile.updated_at = now

        user.display_name = display_name
        user.avatar_url = profile.avatar_url
        user.username = claimed_username
        user.updated_at = now
        await db.commit()
        return self._responses.profile_response(user, profile)

    async def _serialize_posts(
        self,
        db: AsyncSession,
        posts: list[Post],
    ) -> list[CommunityPostResponse]:
        if not posts:
            return []
        author_ids = sorted({post.author_id for post in posts})
        users = {
            user.id: user
            for user in (await db.scalars(select(User).where(User.id.in_(author_ids)))).all()
        }
        profiles = {
            profile.uid: profile
            for profile in (
                await db.scalars(select(CommunityProfile).where(CommunityProfile.uid.in_(author_ids)))
            ).all()
        }
        counts = await self._load_post_reaction_counts(db, [post.id for post in posts])
        return [
            self._responses.post_response(
                post,
                users.get(post.author_id),
                profiles.get(post.author_id),
                counts.get(post.id, empty_reaction_counts()),
            )
            for post in posts
            if users.get(post.author_id) is not None
        ]

    async def _serialize_comments(
        self,
        db: AsyncSession,
        comments: list[Comment],
    ) -> list[CommunityCommentResponse]:
        if not comments:
            return []
        author_ids = sorted({comment.author_id for comment in comments})
        users = {
            user.id: user
            for user in (await db.scalars(select(User).where(User.id.in_(author_ids)))).all()
        }
        profiles = {
            profile.uid: profile
            for profile in (
                await db.scalars(select(CommunityProfile).where(CommunityProfile.uid.in_(author_ids)))
            ).all()
        }
        counts = await self._load_comment_reaction_counts(db, [comment.id for comment in comments])
        return [
            self._responses.comment_response(
                comment,
                users.get(comment.author_id),
                profiles.get(comment.author_id),
                counts.get(comment.id, {}),
            )
            for comment in comments
            if users.get(comment.author_id) is not None
        ]

    async def _serialize_profiles(
        self,
        db: AsyncSession,
        users: list[User],
    ) -> list[CommunityProfileResponse]:
        if not users:
            return []
        user_ids = [user.id for user in users]
        profiles = {
            profile.uid: profile
            for profile in (
                await db.scalars(select(CommunityProfile).where(CommunityProfile.uid.in_(user_ids)))
            ).all()
        }
        return [
            self._responses.profile_response(user, profiles.get(user.id))
            for user in users
        ]

    async def _ensure_profile(self, db: AsyncSession, user: User) -> CommunityProfile:
        existing = await db.get(CommunityProfile, user.id)
        if existing is not None:
            return existing
        display_name = normalize_display_name(user.display_name) or "XR HODL Member"
        desired_username = user.display_name or user.username or user.id
        values = {
            "uid": user.id,
            "display_name": display_name,
            "avatar_url": user.avatar_url,
            "cover_image_url": None,
            "biography": "",
            "birthday_label": "",
            "website": "",
            "social_accounts_json": {},
            "public_watchlist_symbols_json": [],
            "blocked_account_ids_json": [],
            "display_name_change_count": 0,
            "is_pro": self._daily_rewards.is_effective_pro_user(user),
        }
        for _ in range(5):
            claimed_username = await self._claim_username(
                db,
                uid=user.id,
                desired=desired_username,
                strict=False,
            )
            try:
                await db.execute(
                    insert(CommunityProfile).values(
                        **values,
                        username=claimed_username,
                    )
                )
                await db.flush()
                created = await db.get(CommunityProfile, user.id)
                if created is not None:
                    return created
            except IntegrityError as error:
                if not is_profile_username_conflict(error):
                    raise
        raise self._bad_request("Could not reserve a username.")

    async def _claim_username(
        self,
        db: AsyncSession,
        *,
        uid: str,
        desired: str,
        strict: bool,
    ) -> str:
        base = normalize_username(desired)
        if not base:
            base = fallback_username(uid, uid=uid)
        row = await db.scalar(
            select(CommunityProfile.uid).where(
                CommunityProfile.username == base,
                CommunityProfile.uid != uid,
            )
        )
        if row is None:
            return base
        if strict:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Username already in use.")

        suffix_seed = normalize_username(uid) or uid.lower()
        suffix = suffix_seed[:6] or "user"
        for index in range(100):
            suffix_part = suffix if index == 0 else f"{suffix}{index}"
            head = base[: max(1, 24 - len(suffix_part) - 1)]
            candidate = f"{head}_{suffix_part}"[:24]
            row = await db.scalar(
                select(CommunityProfile.uid).where(
                    CommunityProfile.username == candidate,
                    CommunityProfile.uid != uid,
                )
            )
            if row is None:
                return candidate
        raise self._bad_request("Could not reserve a username.")

    async def _load_post_reaction_counts(
        self,
        db: AsyncSession,
        post_ids: list[str],
    ) -> dict[str, dict[str, int]]:
        if not post_ids:
            return {}
        rows = (
            await db.execute(
                select(PostReaction.post_id, PostReaction.reaction_type, func.count(PostReaction.id))
                .where(PostReaction.post_id.in_(post_ids))
                .group_by(PostReaction.post_id, PostReaction.reaction_type)
            )
        ).all()
        grouped = {post_id: empty_reaction_counts() for post_id in post_ids}
        for post_id, reaction_type, count in rows:
            reaction_key = normalize_reaction_key(reaction_type)
            if reaction_key is None:
                continue
            grouped.setdefault(post_id, empty_reaction_counts())[reaction_key] = int(count)
        return grouped

    async def _load_comment_reaction_counts(
        self,
        db: AsyncSession,
        comment_ids: list[str],
    ) -> dict[str, dict[int, int]]:
        if not comment_ids:
            return {}
        rows = (
            await db.execute(
                select(CommentReaction.comment_id, CommentReaction.reaction_code, func.count(CommentReaction.id))
                .where(CommentReaction.comment_id.in_(comment_ids))
                .group_by(CommentReaction.comment_id, CommentReaction.reaction_code)
            )
        ).all()
        grouped: dict[str, dict[int, int]] = {}
        for comment_id, reaction_code, count in rows:
            grouped.setdefault(comment_id, {})[int(reaction_code)] = int(count)
        return grouped

    async def _count_follows(self, db: AsyncSession, *, column_name: str, uid: str) -> int:
        normalized_uid = uid.strip()
        if not normalized_uid:
            return 0
        predicate = (
            CommunityFollow.following_uid == normalized_uid
            if column_name == "following"
            else CommunityFollow.follower_uid == normalized_uid
        )
        count = await db.scalar(select(func.count(CommunityFollow.id)).where(predicate))
        return int(count or 0)

    async def _notify_followers_of_new_post(
        self,
        db: AsyncSession,
        *,
        actor: User,
        post: CommunityPostResponse,
    ) -> None:
        if self._notifications is None:
            return
        follower_ids = list(
            (
                await db.scalars(
                    select(CommunityFollow.follower_uid).where(CommunityFollow.following_uid == actor.id)
                )
            ).all()
        )
        headline = post.content if len(post.content) <= 96 else f"{post.content[:96].rstrip()}..."
        for follower_id in follower_ids:
            if follower_id == actor.id:
                continue
            notification = await self._notifications.create_notification(
                db,
                user_id=follower_id,
                kind="community_post",
                title="New community post",
                body=f"{post.authorName} posted: {headline}",
                actor_uid=actor.id,
                post_id=post.id,
            )
            await self._publish_notification(db, follower_id, notification)

    async def _publish_notification(self, db: AsyncSession, user_id: str, notification) -> None:
        if self._bus is None or self._notifications is None:
            return
        await self._bus.publish(
            f"user:{user_id}",
            WsEnvelope(
                type="notification.new",
                topic=f"user:{user_id}",
                data={"notification": await self._notifications.serialize_notification(db, notification)},
            ).model_dump(mode="json"),
        )

    async def _publish_feed_post_created(self, post: CommunityPostResponse) -> None:
        if self._bus is None:
            return
        await self._bus.publish(
            "feed:global",
            WsEnvelope(
                type="post.created",
                topic="feed:global",
                data=post.model_dump(mode="json"),
            ).model_dump(mode="json"),
        )

    def _now(self) -> datetime:
        return datetime.now(timezone.utc)

    def _bad_request(self, detail: str) -> HTTPException:
        return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=detail)

    def _not_found(self, detail: str) -> HTTPException:
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=detail)

    def _forbidden(self, detail: str) -> HTTPException:
        return HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=detail)

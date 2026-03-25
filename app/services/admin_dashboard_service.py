from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.entities import (
    AuthSession,
    Chat,
    Comment,
    CommentReaction,
    CommunityFollow,
    LearningVideoLesson,
    Message,
    NewsArticle,
    NewsArticleTranslation,
    Notification,
    Post,
    PostReaction,
    PostView,
    PushToken,
    User,
)
from app.schemas.admin_dashboard import (
    AdminKeyMetricItem,
    AdminMessageTypeItem,
    AdminNamedValueItem,
    AdminOverviewResponse,
    AdminSentimentItem,
    AdminStatsResponse,
    AdminTimePoint,
    AdminTopSymbolItem,
    AdminWeeklyEngagementItem,
)


_DAY_NAME = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


class AdminDashboardService:
    async def get_stats(self, db: AsyncSession) -> AdminStatsResponse:
        now = self._utc_now()
        active_cutoff = now - timedelta(minutes=5)

        total_users = await self._count(db, select(func.count(User.id)))
        free_users = await self._count(db, select(func.count(User.id)).where(User.membership_tier == "free"))
        pro_tier_users = await self._count(db, select(func.count(User.id)).where(User.membership_tier == "pro"))
        legend_users = await self._count(db, select(func.count(User.id)).where(User.membership_tier == "legend"))
        pro_users = await self._count(db, select(func.count(User.id)).where(User.is_pro.is_(True)))
        total_posts = await self._count(db, select(func.count(Post.id)))
        total_comments = await self._count(db, select(func.count(Comment.id)))
        total_post_views = await self._count(db, select(func.coalesce(func.sum(Post.view_count), 0)))
        total_messages = await self._count(db, select(func.count(Message.id)))
        voice_messages = await self._count(db, select(func.count(Message.id)).where(Message.message_type == "voice"))
        deleted_messages = await self._count(db, select(func.count(Message.id)).where(Message.deleted_at.is_not(None)))
        total_chats = await self._count(db, select(func.count(Chat.id)))
        active_sessions = await self._count(
            db,
            select(func.count(AuthSession.id))
            .where(AuthSession.access_expires_at > now)
            .where(AuthSession.last_seen_at >= active_cutoff),
        )
        total_lessons = await self._count(db, select(func.count(LearningVideoLesson.id)))
        published_lessons = await self._count(
            db,
            select(func.count(LearningVideoLesson.id)).where(LearningVideoLesson.is_published.is_(True)),
        )
        total_news_articles = await self._count(db, select(func.count(NewsArticle.id)))
        translated_articles = await self._count(db, select(func.count(NewsArticleTranslation.id)))
        total_notifications = await self._count(db, select(func.count(Notification.id)))
        total_reactions = (
            await self._count(db, select(func.count(PostReaction.id)))
            + await self._count(db, select(func.count(CommentReaction.id)))
        )
        total_follows = await self._count(db, select(func.count(CommunityFollow.id)))

        return AdminStatsResponse(
            totalUsers=total_users,
            freeUsers=free_users,
            proTierUsers=pro_tier_users,
            legendUsers=legend_users,
            proUsers=pro_users,
            totalPosts=total_posts,
            totalComments=total_comments,
            totalPostViews=total_post_views,
            totalMessages=total_messages,
            voiceMessages=voice_messages,
            deletedMessages=deleted_messages,
            totalChats=total_chats,
            activeSessions=active_sessions,
            totalLessons=total_lessons,
            publishedLessons=published_lessons,
            totalNewsArticles=total_news_articles,
            translatedArticles=translated_articles,
            totalNotifications=total_notifications,
            totalReactions=total_reactions,
            totalFollows=total_follows,
            updatedAt=now,
        )

    async def get_overview(
        self,
        db: AsyncSession,
        *,
        days: int,
    ) -> AdminOverviewResponse:
        days_clamped = self._clamp_int(days, low=1, high=90)
        from_dt = self._since_dt_for_days(days_clamped)

        stats = await self.get_stats(db)
        user_series = await self._count_series_by_day(db, column=User.created_at, from_dt=from_dt, days=days_clamped)
        post_series = await self._count_series_by_day(db, column=Post.created_at, from_dt=from_dt, days=days_clamped)
        message_series = await self._count_series_by_day(
            db,
            column=Message.created_at,
            from_dt=from_dt,
            days=days_clamped,
        )

        week_from_dt = self._since_dt_for_days(7)
        post_week = await self._count_series_by_day(db, column=Post.created_at, from_dt=week_from_dt, days=7)
        comment_week = await self._count_series_by_day(db, column=Comment.created_at, from_dt=week_from_dt, days=7)
        message_week = await self._count_series_by_day(db, column=Message.created_at, from_dt=week_from_dt, days=7)

        weekly_engagement = [
            AdminWeeklyEngagementItem(
                day=_DAY_NAME[day.weekday()],
                posts=post_week[i].value if i < len(post_week) else 0,
                comments=comment_week[i].value if i < len(comment_week) else 0,
                messages=message_week[i].value if i < len(message_week) else 0,
            )
            for i, day in enumerate(self._date_range(self._utc_now().date(), days=7))
        ]

        window_total_posts = await self._count(db, select(func.count(Post.id)).where(Post.created_at >= from_dt))
        bullish = await self._count(
            db,
            select(func.count(Post.id))
            .where(Post.created_at >= from_dt)
            .where(Post.market_bias == "bullish"),
        )
        bearish = await self._count(
            db,
            select(func.count(Post.id))
            .where(Post.created_at >= from_dt)
            .where(Post.market_bias == "bearish"),
        )
        neutral = max(0, int(window_total_posts - bullish - bearish))
        sentiment = [
            AdminSentimentItem(name="Bullish", value=bullish, color="#10b981"),
            AdminSentimentItem(name="Bearish", value=bearish, color="#ef4444"),
            AdminSentimentItem(name="Neutral", value=neutral, color="#64748b"),
        ]

        type_rows = (
            await db.execute(
                select(Message.message_type, func.count(Message.id))
                .group_by(Message.message_type)
            )
        ).all()
        type_counts = {str(t or "text"): int(c or 0) for t, c in type_rows}
        reply_count = await self._count(
            db,
            select(func.count(Message.id)).where(Message.reply_to_message_id.is_not(None)),
        )
        message_types = [
            AdminMessageTypeItem(type="Text", count=type_counts.get("text", 0), color="#3b82f6"),
            AdminMessageTypeItem(type="Image", count=type_counts.get("image", 0), color="#10b981"),
            AdminMessageTypeItem(type="Voice", count=type_counts.get("voice", 0), color="#8b5cf6"),
            AdminMessageTypeItem(type="Reply", count=reply_count, color="#f59e0b"),
        ]

        posts_expr = func.count(Post.id).label("posts")
        views_expr = func.coalesce(func.sum(Post.view_count), 0).label("views")
        reactions_expr = func.count(PostReaction.id).label("reactions")
        symbol_rows = (
            await db.execute(
                select(
                    Post.symbol,
                    posts_expr,
                    views_expr,
                    reactions_expr,
                )
                .select_from(Post)
                .outerjoin(PostReaction, PostReaction.post_id == Post.id)
                .where(Post.created_at >= from_dt)
                .where(Post.symbol.is_not(None))
                .where(Post.symbol != "")
                .group_by(Post.symbol)
                .order_by(desc(posts_expr))
                .limit(5)
            )
        ).all()
        palette = ["#f59e0b", "#3b82f6", "#8b5cf6", "#06b6d4", "#f97316"]
        top_symbols = [
            AdminTopSymbolItem(
                symbol=str(row.symbol),
                posts=int(row.posts or 0),
                views=int(row.views or 0),
                reactions=int(row.reactions or 0),
                color=palette[i % len(palette)],
            )
            for i, row in enumerate(symbol_rows)
        ]

        platform_rows = (
            await db.execute(
                select(PushToken.platform, func.count(PushToken.token)).group_by(PushToken.platform)
            )
        ).all()
        platform_counts = {str(p or "unknown").lower(): int(c or 0) for p, c in platform_rows}
        platform_distribution = [
            AdminNamedValueItem(name="iOS", value=platform_counts.get("ios", 0), color="#3b82f6"),
            AdminNamedValueItem(name="Android", value=platform_counts.get("android", 0), color="#10b981"),
            AdminNamedValueItem(name="Web", value=platform_counts.get("web", 0), color="#8b5cf6"),
        ]
        other_value = sum(v for k, v in platform_counts.items() if k not in {"ios", "android", "web"} and v)
        if other_value:
            platform_distribution.append(AdminNamedValueItem(name="Other", value=other_value, color="#64748b"))

        now = self._utc_now()
        day_cutoff = now - timedelta(days=1)
        active_users_24h = await self._count(
            db,
            select(func.count(func.distinct(AuthSession.user_id)))
            .where(AuthSession.access_expires_at > now)
            .where(AuthSession.last_seen_at >= day_cutoff),
        )
        avg_session_seconds = float(
            (
                await db.execute(
                    select(
                        func.coalesce(
                            func.avg(
                                func.extract("epoch", AuthSession.last_seen_at - AuthSession.created_at)
                            ),
                            0,
                        )
                    )
                    .where(AuthSession.last_seen_at >= from_dt)
                    .where(AuthSession.last_seen_at.is_not(None))
                )
            ).scalar()
            or 0
        )
        if avg_session_seconds < 0:
            avg_session_seconds = 0
        avg_minutes = int(avg_session_seconds // 60)
        avg_seconds = int(avg_session_seconds % 60)
        avg_session_value = f"{avg_minutes}m {avg_seconds:02d}s"
        avg_session_pct = int(self._clamp_int(round((avg_session_seconds / (15 * 60)) * 100), low=0, high=100))

        window_views = await self._count(db, select(func.count(PostView.id)).where(PostView.created_at >= from_dt))
        window_comments = await self._count(db, select(func.count(Comment.id)).where(Comment.created_at >= from_dt))
        window_post_reactions = await self._count(db, select(func.count(PostReaction.id)).where(PostReaction.created_at >= from_dt))
        window_comment_reactions = await self._count(
            db,
            select(func.count(CommentReaction.id)).where(CommentReaction.created_at >= from_dt),
        )
        window_interactions = window_comments + window_post_reactions + window_comment_reactions
        engagement_rate = (window_interactions / window_views) * 100 if window_views else 0.0
        engagement_pct = int(self._clamp_int(round(engagement_rate), low=0, high=100))

        pro_conversion_rate = (stats.proTierUsers / stats.totalUsers) * 100 if stats.totalUsers else 0.0
        pro_conversion_pct = int(self._clamp_int(round(pro_conversion_rate), low=0, high=100))

        window_notifications = await self._count(db, select(func.count(Notification.id)).where(Notification.created_at >= from_dt))
        window_notifications_read = await self._count(
            db,
            select(func.count(Notification.id))
            .where(Notification.created_at >= from_dt)
            .where(Notification.is_read.is_(True)),
        )
        notif_read_rate = (window_notifications_read / window_notifications) * 100 if window_notifications else 0.0
        notif_read_pct = int(self._clamp_int(round(notif_read_rate), low=0, high=100))
        dau_pct = (
            int(self._clamp_int(round((active_users_24h / stats.totalUsers) * 100), low=0, high=100))
            if stats.totalUsers
            else 0
        )

        key_metrics = [
            AdminKeyMetricItem(label="Daily Active Users", value=f"{active_users_24h:,}", pct=dau_pct),
            AdminKeyMetricItem(label="Avg Session Duration", value=avg_session_value, pct=avg_session_pct),
            AdminKeyMetricItem(label="Post Engagement Rate", value=f"{engagement_rate:.1f}%", pct=engagement_pct),
            AdminKeyMetricItem(label="PRO Conversion Rate", value=f"{pro_conversion_rate:.1f}%", pct=pro_conversion_pct),
            AdminKeyMetricItem(label="Notification Read Rate", value=f"{notif_read_rate:.1f}%", pct=notif_read_pct),
        ]

        return AdminOverviewResponse(
            stats=stats,
            userSeries=user_series,
            postSeries=post_series,
            messageSeries=message_series,
            weeklyEngagement=weekly_engagement,
            sentiment=sentiment,
            messageTypes=message_types,
            topSymbols=top_symbols,
            platformDistribution=platform_distribution,
            keyMetrics=key_metrics,
            updatedAt=self._utc_now(),
        )

    async def _count(self, db: AsyncSession, stmt) -> int:
        return int((await db.execute(stmt)).scalar() or 0)

    async def _count_series_by_day(
        self,
        db: AsyncSession,
        *,
        column,
        from_dt: datetime,
        days: int,
    ) -> list[AdminTimePoint]:
        end = self._utc_now().date()
        date_list = self._date_range(end, days=days)
        if not date_list:
            return []
        rows = (
            await db.execute(
                select(func.date_trunc("day", column).label("day"), func.count().label("count"))
                .where(column >= from_dt)
                .group_by("day")
                .order_by("day")
            )
        ).all()
        mapping: dict[str, int] = {}
        for row in rows:
            day = row.day
            if day is None:
                continue
            key = day.date().isoformat()
            mapping[key] = int(row.count or 0)
        return [AdminTimePoint(date=d.isoformat(), value=mapping.get(d.isoformat(), 0)) for d in date_list]

    def _utc_now(self) -> datetime:
        return datetime.now(timezone.utc)

    def _clamp_int(self, value: int, *, low: int, high: int) -> int:
        return max(low, min(int(value), high))

    def _date_range(self, end: date, *, days: int) -> list[date]:
        if days <= 0:
            return []
        start = end - timedelta(days=days - 1)
        return [start + timedelta(days=i) for i in range(days)]

    def _since_dt_for_days(self, days: int) -> datetime:
        end = self._utc_now().date()
        start = end - timedelta(days=days - 1)
        return datetime(start.year, start.month, start.day, tzinfo=timezone.utc)

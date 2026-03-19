from __future__ import annotations

import secrets
from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import Response
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from pydantic import BaseModel, Field
from sqlalchemy import delete, desc, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.db.session import get_db
from app.models.entities import (
    AiProviderConfig,
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
    PollVote,
    PushToken,
    User,
)
from app.services.ai_provider_config_service import (
    DEFAULT_GEMINI_MODEL,
    MAX_GEMINI_API_KEYS,
    count_gemini_config_rows,
    mask_api_key,
    place_gemini_config,
    rebalance_gemini_config_rows,
)

router = APIRouter(prefix="/admin", tags=["admin"])

_security = HTTPBasic()

_DAY_NAME = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _clamp_int(value: int, *, low: int, high: int) -> int:
    return max(low, min(int(value), high))


def _date_range(end: date, *, days: int) -> list[date]:
    if days <= 0:
        return []
    start = end - timedelta(days=days - 1)
    return [start + timedelta(days=i) for i in range(days)]


def _since_dt_for_days(days: int) -> datetime:
    end = _utc_now().date()
    start = end - timedelta(days=days - 1)
    return datetime(start.year, start.month, start.day, tzinfo=timezone.utc)


class AdminTimePoint(BaseModel):
    date: str
    value: int


class AdminWeeklyEngagementItem(BaseModel):
    day: str
    posts: int = 0
    comments: int = 0
    messages: int = 0


class AdminSentimentItem(BaseModel):
    name: str
    value: int
    color: str


class AdminMessageTypeItem(BaseModel):
    type: str
    count: int
    color: str


class AdminTopSymbolItem(BaseModel):
    symbol: str
    posts: int
    views: int
    reactions: int
    color: str = "#3b82f6"


class AdminNamedValueItem(BaseModel):
    name: str
    value: int
    color: str = "#64748b"


class AdminKeyMetricItem(BaseModel):
    label: str
    value: str
    pct: int = 0


class AdminOverviewResponse(BaseModel):
    stats: AdminStatsResponse
    userSeries: list[AdminTimePoint] = Field(default_factory=list)
    postSeries: list[AdminTimePoint] = Field(default_factory=list)
    messageSeries: list[AdminTimePoint] = Field(default_factory=list)
    weeklyEngagement: list[AdminWeeklyEngagementItem] = Field(default_factory=list)
    sentiment: list[AdminSentimentItem] = Field(default_factory=list)
    messageTypes: list[AdminMessageTypeItem] = Field(default_factory=list)
    topSymbols: list[AdminTopSymbolItem] = Field(default_factory=list)
    platformDistribution: list[AdminNamedValueItem] = Field(default_factory=list)
    keyMetrics: list[AdminKeyMetricItem] = Field(default_factory=list)
    updatedAt: datetime = Field(default_factory=_utc_now)


def _require_admin(credentials: HTTPBasicCredentials = Depends(_security)) -> str:
    settings = get_settings()
    is_valid_username = secrets.compare_digest(
        credentials.username,
        settings.admin_panel_username,
    )
    is_valid_password = secrets.compare_digest(
        credentials.password,
        settings.admin_panel_password,
    )
    if is_valid_username and is_valid_password:
        return credentials.username
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid admin credentials.",
        headers={"WWW-Authenticate": "Basic"},
    )


class AdminStatsResponse(BaseModel):
    totalUsers: int = 0
    freeUsers: int = 0
    proTierUsers: int = 0
    legendUsers: int = 0
    proUsers: int = 0
    totalPosts: int = 0
    totalComments: int = 0
    totalPostViews: int = 0
    totalMessages: int = 0
    voiceMessages: int = 0
    deletedMessages: int = 0
    totalChats: int = 0
    activeSessions: int = 0
    totalLessons: int = 0
    publishedLessons: int = 0
    totalNewsArticles: int = 0
    translatedArticles: int = 0
    totalNotifications: int = 0
    totalReactions: int = 0
    totalFollows: int = 0
    updatedAt: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class AdminUserItem(BaseModel):
    id: str
    username: str
    display_name: str
    membership_tier: str
    is_pro: bool
    diamonds_balance: int
    daily_reward_streak: int
    created_at: datetime
    posts: int = 0
    followers: int = 0


class AdminUserListResponse(BaseModel):
    items: list[AdminUserItem] = Field(default_factory=list)
    total: int = 0


class AdminUserPatch(BaseModel):
    display_name: str | None = None
    membership_tier: str | None = None
    is_pro: bool | None = None
    diamonds_balance: int | None = None
    daily_reward_streak: int | None = None


class AdminAuthSessionItem(BaseModel):
    id: str
    user_id: str
    username: str
    created_at: datetime
    last_seen_at: datetime
    access_expires_at: datetime


class AdminAuthSessionListResponse(BaseModel):
    items: list[AdminAuthSessionItem] = Field(default_factory=list)
    total: int = 0


class AiProviderConfigCreate(BaseModel):
    label: str | None = None
    api_key: str
    model: str = DEFAULT_GEMINI_MODEL
    enabled: bool = True
    sort_order: int = Field(default=1, ge=1, le=MAX_GEMINI_API_KEYS)


class AiProviderConfigPatch(BaseModel):
    label: str | None = None
    api_key: str | None = None
    model: str | None = None
    enabled: bool | None = None
    sort_order: int | None = Field(default=None, ge=1, le=MAX_GEMINI_API_KEYS)


class AiProviderConfigOut(BaseModel):
    id: int
    provider: str
    label: str
    model: str
    sort_order: int
    enabled: bool
    updated_at: datetime
    has_api_key: bool = False
    api_key_hint: str = ""


class AdminPostItem(BaseModel):
    id: str
    author_uid: str
    author_username: str
    author_display_name: str
    content: str
    symbol: str | None = None
    market_bias: str | None = None
    comment_count: int = 0
    view_count: int = 0
    created_at: datetime


class AdminPostListResponse(BaseModel):
    items: list[AdminPostItem] = Field(default_factory=list)
    total: int = 0


class AdminNewsArticleItem(BaseModel):
    id: int
    source: str
    url: str
    raw_title: str
    is_liquidation: bool = False
    published_at: datetime | None = None
    released_at: datetime | None = None
    translations: int = 0


class AdminNewsArticleListResponse(BaseModel):
    items: list[AdminNewsArticleItem] = Field(default_factory=list)
    total: int = 0


class AdminNotificationItem(BaseModel):
    id: str
    user_id: str
    username: str | None = None
    event_type: str
    is_read: bool = False
    created_at: datetime


class AdminNotificationListResponse(BaseModel):
    items: list[AdminNotificationItem] = Field(default_factory=list)
    total: int = 0


@router.get("/stats", response_model=AdminStatsResponse)
async def get_admin_stats(
    _: str = Depends(_require_admin),
    db: AsyncSession = Depends(get_db),
) -> AdminStatsResponse:
    now = _utc_now()
    active_cutoff = now - timedelta(minutes=5)

    async def count(stmt) -> int:
        return int((await db.execute(stmt)).scalar() or 0)

    total_users = await count(select(func.count(User.id)))
    free_users = await count(select(func.count(User.id)).where(User.membership_tier == "free"))
    pro_tier_users = await count(select(func.count(User.id)).where(User.membership_tier == "pro"))
    legend_users = await count(select(func.count(User.id)).where(User.membership_tier == "legend"))
    pro_users = await count(select(func.count(User.id)).where(User.is_pro.is_(True)))
    total_posts = await count(select(func.count(Post.id)))
    total_comments = await count(select(func.count(Comment.id)))
    total_post_views = await count(select(func.coalesce(func.sum(Post.view_count), 0)))
    total_messages = await count(select(func.count(Message.id)))
    voice_messages = await count(select(func.count(Message.id)).where(Message.message_type == "voice"))
    deleted_messages = await count(select(func.count(Message.id)).where(Message.deleted_at.is_not(None)))
    total_chats = await count(select(func.count(Chat.id)))
    active_sessions = await count(
        select(func.count(AuthSession.id))
        .where(AuthSession.access_expires_at > now)
        .where(AuthSession.last_seen_at >= active_cutoff)
    )
    total_lessons = await count(select(func.count(LearningVideoLesson.id)))
    published_lessons = await count(
        select(func.count(LearningVideoLesson.id)).where(LearningVideoLesson.is_published.is_(True))
    )
    total_news_articles = await count(select(func.count(NewsArticle.id)))
    translated_articles = await count(select(func.count(NewsArticleTranslation.id)))
    total_notifications = await count(select(func.count(Notification.id)))
    total_reactions = (
        await count(select(func.count(PostReaction.id)))
        + await count(select(func.count(CommentReaction.id)))
    )
    total_follows = await count(select(func.count(CommunityFollow.id)))

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


async def _count_series_by_day(
    db: AsyncSession,
    *,
    column,
    from_dt: datetime,
    days: int,
) -> list[AdminTimePoint]:
    end = _utc_now().date()
    date_list = _date_range(end, days=days)
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

    return [
        AdminTimePoint(date=d.isoformat(), value=mapping.get(d.isoformat(), 0))
        for d in date_list
    ]


@router.get("/overview", response_model=AdminOverviewResponse)
async def get_admin_overview(
    days: int = Query(default=30, ge=1, le=90),
    _: str = Depends(_require_admin),
    db: AsyncSession = Depends(get_db),
) -> AdminOverviewResponse:
    days_clamped = _clamp_int(days, low=1, high=90)
    from_dt = _since_dt_for_days(days_clamped)

    stats = await get_admin_stats(_, db)

    user_series = await _count_series_by_day(db, column=User.created_at, from_dt=from_dt, days=days_clamped)
    post_series = await _count_series_by_day(db, column=Post.created_at, from_dt=from_dt, days=days_clamped)
    message_series = await _count_series_by_day(db, column=Message.created_at, from_dt=from_dt, days=days_clamped)

    week_from_dt = _since_dt_for_days(7)
    post_week = await _count_series_by_day(db, column=Post.created_at, from_dt=week_from_dt, days=7)
    comment_week = await _count_series_by_day(db, column=Comment.created_at, from_dt=week_from_dt, days=7)
    message_week = await _count_series_by_day(db, column=Message.created_at, from_dt=week_from_dt, days=7)

    weekly_engagement: list[AdminWeeklyEngagementItem] = []
    for i, day in enumerate(_date_range(_utc_now().date(), days=7)):
        weekly_engagement.append(
            AdminWeeklyEngagementItem(
                day=_DAY_NAME[day.weekday()],
                posts=post_week[i].value if i < len(post_week) else 0,
                comments=comment_week[i].value if i < len(comment_week) else 0,
                messages=message_week[i].value if i < len(message_week) else 0,
            )
        )

    async def count(stmt) -> int:
        return int((await db.execute(stmt)).scalar() or 0)

    window_total_posts = await count(select(func.count(Post.id)).where(Post.created_at >= from_dt))
    bullish = await count(
        select(func.count(Post.id))
        .where(Post.created_at >= from_dt)
        .where(Post.market_bias == "bullish")
    )
    bearish = await count(
        select(func.count(Post.id))
        .where(Post.created_at >= from_dt)
        .where(Post.market_bias == "bearish")
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
    reply_count = await count(select(func.count(Message.id)).where(Message.reply_to_message_id.is_not(None)))

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
    top_symbols: list[AdminTopSymbolItem] = []
    for i, row in enumerate(symbol_rows):
        top_symbols.append(
            AdminTopSymbolItem(
                symbol=str(row.symbol),
                posts=int(row.posts or 0),
                views=int(row.views or 0),
                reactions=int(row.reactions or 0),
                color=palette[i % len(palette)],
            )
        )

    platform_rows = (
        await db.execute(
            select(PushToken.platform, func.count(PushToken.token)).group_by(PushToken.platform)
        )
    ).all()
    platform_counts = {str(p or "unknown").lower(): int(c or 0) for p, c in platform_rows}
    platform_palette = {
        "ios": "#3b82f6",
        "android": "#10b981",
        "web": "#8b5cf6",
        "unknown": "#64748b",
        "other": "#64748b",
    }
    platform_distribution = [
        AdminNamedValueItem(name="iOS", value=platform_counts.get("ios", 0), color=platform_palette["ios"]),
        AdminNamedValueItem(
            name="Android", value=platform_counts.get("android", 0), color=platform_palette["android"]
        ),
        AdminNamedValueItem(name="Web", value=platform_counts.get("web", 0), color=platform_palette["web"]),
    ]
    other_value = sum(v for k, v in platform_counts.items() if k not in {"ios", "android", "web"} and v)
    if other_value:
        platform_distribution.append(
            AdminNamedValueItem(name="Other", value=other_value, color=platform_palette["other"])
        )

    now = _utc_now()
    day_cutoff = now - timedelta(days=1)
    active_users_24h = await count(
        select(func.count(func.distinct(AuthSession.user_id)))
        .where(AuthSession.access_expires_at > now)
        .where(AuthSession.last_seen_at >= day_cutoff)
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
    avg_session_pct = int(_clamp_int(round((avg_session_seconds / (15 * 60)) * 100), low=0, high=100))

    window_views = await count(select(func.count(PostView.id)).where(PostView.created_at >= from_dt))
    window_comments = await count(select(func.count(Comment.id)).where(Comment.created_at >= from_dt))
    window_post_reactions = await count(select(func.count(PostReaction.id)).where(PostReaction.created_at >= from_dt))
    window_comment_reactions = await count(
        select(func.count(CommentReaction.id)).where(CommentReaction.created_at >= from_dt)
    )
    window_interactions = window_comments + window_post_reactions + window_comment_reactions
    engagement_rate = (window_interactions / window_views) * 100 if window_views else 0.0
    engagement_pct = int(_clamp_int(round(engagement_rate), low=0, high=100))

    pro_conversion_rate = (stats.proTierUsers / stats.totalUsers) * 100 if stats.totalUsers else 0.0
    pro_conversion_pct = int(_clamp_int(round(pro_conversion_rate), low=0, high=100))

    window_notifications = await count(select(func.count(Notification.id)).where(Notification.created_at >= from_dt))
    window_notifications_read = await count(
        select(func.count(Notification.id))
        .where(Notification.created_at >= from_dt)
        .where(Notification.is_read.is_(True))
    )
    notif_read_rate = (window_notifications_read / window_notifications) * 100 if window_notifications else 0.0
    notif_read_pct = int(_clamp_int(round(notif_read_rate), low=0, high=100))

    dau_pct = (
        int(_clamp_int(round((active_users_24h / stats.totalUsers) * 100), low=0, high=100))
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
        updatedAt=_utc_now(),
    )


@router.get("/users", response_model=AdminUserListResponse)
async def list_admin_users(
    q: str = Query(default=""),
    filter: str = Query(default="all"),
    limit: int = Query(default=25, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    _: str = Depends(_require_admin),
    db: AsyncSession = Depends(get_db),
) -> AdminUserListResponse:
    q_norm = (q or "").strip()
    filter_norm = (filter or "all").strip().lower()

    posts_count = (
        select(func.count(Post.id))
        .where(Post.author_id == User.id)
        .correlate(User)
        .scalar_subquery()
    )
    followers_count = (
        select(func.count(CommunityFollow.id))
        .where(CommunityFollow.following_uid == User.id)
        .correlate(User)
        .scalar_subquery()
    )

    where = []
    if q_norm:
        like = f"%{q_norm}%"
        where.append(or_(User.username.ilike(like), User.display_name.ilike(like)))
    if filter_norm == "pro":
        where.append(User.is_pro.is_(True))
    elif filter_norm == "free":
        where.append(User.is_pro.is_(False))

    total_stmt = select(func.count(User.id))
    if where:
        total_stmt = total_stmt.where(*where)
    total = int((await db.execute(total_stmt)).scalar() or 0)

    stmt = (
        select(
            User.id,
            User.username,
            User.display_name,
            User.membership_tier,
            User.is_pro,
            User.diamonds_balance,
            User.daily_reward_streak,
            User.created_at,
            posts_count.label("posts"),
            followers_count.label("followers"),
        )
        .order_by(User.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    if where:
        stmt = stmt.where(*where)

    rows = (await db.execute(stmt)).all()
    items = [
        AdminUserItem(
            id=row.id,
            username=row.username,
            display_name=row.display_name,
            membership_tier=row.membership_tier,
            is_pro=bool(row.is_pro),
            diamonds_balance=int(row.diamonds_balance or 0),
            daily_reward_streak=int(row.daily_reward_streak or 0),
            created_at=row.created_at,
            posts=int(row.posts or 0),
            followers=int(row.followers or 0),
        )
        for row in rows
    ]
    return AdminUserListResponse(items=items, total=total)


@router.put("/users/{user_id}", response_model=AdminUserItem)
async def update_admin_user(
    user_id: str,
    payload: AdminUserPatch,
    _: str = Depends(_require_admin),
    db: AsyncSession = Depends(get_db),
) -> AdminUserItem:
    user = await db.get(User, user_id.strip())
    if user is None:
        raise HTTPException(status_code=404, detail="User not found.")

    if payload.display_name is not None:
        user.display_name = payload.display_name.strip() or user.display_name
    if payload.membership_tier is not None:
        user.membership_tier = payload.membership_tier.strip().lower() or user.membership_tier
    if payload.is_pro is not None:
        user.is_pro = bool(payload.is_pro)
    if payload.diamonds_balance is not None:
        user.diamonds_balance = int(payload.diamonds_balance)
    if payload.daily_reward_streak is not None:
        user.daily_reward_streak = int(payload.daily_reward_streak)

    await db.commit()
    await db.refresh(user)

    posts = int(
        (await db.execute(select(func.count(Post.id)).where(Post.author_id == user.id))).scalar()
        or 0
    )
    followers = int(
        (
            await db.execute(
                select(func.count(CommunityFollow.id)).where(CommunityFollow.following_uid == user.id)
            )
        ).scalar()
        or 0
    )

    return AdminUserItem(
        id=user.id,
        username=user.username,
        display_name=user.display_name,
        membership_tier=user.membership_tier,
        is_pro=bool(user.is_pro),
        diamonds_balance=int(user.diamonds_balance or 0),
        daily_reward_streak=int(user.daily_reward_streak or 0),
        created_at=user.created_at,
        posts=posts,
        followers=followers,
    )


@router.get("/auth-sessions", response_model=AdminAuthSessionListResponse)
async def list_admin_auth_sessions(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    _: str = Depends(_require_admin),
    db: AsyncSession = Depends(get_db),
) -> AdminAuthSessionListResponse:
    total = int((await db.execute(select(func.count(AuthSession.id)))).scalar() or 0)

    stmt = (
        select(
            AuthSession.id,
            AuthSession.user_id,
            User.username,
            AuthSession.created_at,
            AuthSession.last_seen_at,
            AuthSession.access_expires_at,
        )
        .join(User, User.id == AuthSession.user_id)
        .order_by(AuthSession.last_seen_at.desc())
        .limit(limit)
        .offset(offset)
    )
    rows = (await db.execute(stmt)).all()
    return AdminAuthSessionListResponse(
        total=total,
        items=[
            AdminAuthSessionItem(
                id=r.id,
                user_id=r.user_id,
                username=r.username,
                created_at=r.created_at,
                last_seen_at=r.last_seen_at,
                access_expires_at=r.access_expires_at,
            )
            for r in rows
        ],
    )


@router.delete(
    "/auth-sessions/{session_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
)
async def revoke_admin_auth_session(
    session_id: str,
    _: str = Depends(_require_admin),
    db: AsyncSession = Depends(get_db),
) -> Response:
    session = await db.get(AuthSession, session_id.strip())
    if session is None:
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    await db.delete(session)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/posts", response_model=AdminPostListResponse)
async def list_admin_posts(
    q: str = Query(default=""),
    limit: int = Query(default=25, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    _: str = Depends(_require_admin),
    db: AsyncSession = Depends(get_db),
) -> AdminPostListResponse:
    q_norm = (q or "").strip()
    where = []
    if q_norm:
        like = f"%{q_norm}%"
        where.append(or_(Post.content.ilike(like), User.username.ilike(like)))

    total_stmt = select(func.count(Post.id)).select_from(Post).join(User, User.id == Post.author_id)
    if where:
        total_stmt = total_stmt.where(*where)
    total = int((await db.execute(total_stmt)).scalar() or 0)

    stmt = (
        select(
            Post.id,
            Post.author_id,
            User.username,
            User.display_name,
            Post.content,
            Post.symbol,
            Post.market_bias,
            Post.comment_count,
            Post.view_count,
            Post.created_at,
        )
        .select_from(Post)
        .join(User, User.id == Post.author_id)
        .order_by(Post.created_at.desc(), Post.id.desc())
        .limit(limit)
        .offset(offset)
    )
    if where:
        stmt = stmt.where(*where)
    rows = (await db.execute(stmt)).all()
    items = [
        AdminPostItem(
            id=r.id,
            author_uid=r.author_id,
            author_username=r.username,
            author_display_name=r.display_name,
            content=(r.content or "")[:5000],
            symbol=r.symbol,
            market_bias=r.market_bias,
            comment_count=int(r.comment_count or 0),
            view_count=int(r.view_count or 0),
            created_at=r.created_at,
        )
        for r in rows
    ]
    return AdminPostListResponse(items=items, total=total)


@router.delete(
    "/posts/{post_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
)
async def delete_admin_post(
    post_id: str,
    _: str = Depends(_require_admin),
    db: AsyncSession = Depends(get_db),
) -> Response:
    pid = post_id.strip()
    if not pid:
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    post = await db.get(Post, pid)
    if post is None:
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    comment_ids_subq = select(Comment.id).where(Comment.post_id == pid)
    await db.execute(delete(CommentReaction).where(CommentReaction.comment_id.in_(comment_ids_subq)))
    await db.execute(delete(Comment).where(Comment.post_id == pid))
    await db.execute(delete(PollVote).where(PollVote.post_id == pid))
    await db.execute(delete(PostView).where(PostView.post_id == pid))
    await db.execute(delete(PostReaction).where(PostReaction.post_id == pid))
    await db.delete(post)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/news-articles", response_model=AdminNewsArticleListResponse)
async def list_admin_news_articles(
    q: str = Query(default=""),
    filter: str = Query(default="all"),
    limit: int = Query(default=25, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    _: str = Depends(_require_admin),
    db: AsyncSession = Depends(get_db),
) -> AdminNewsArticleListResponse:
    q_norm = (q or "").strip()
    filter_norm = (filter or "all").strip().lower()

    where = []
    if q_norm:
        like = f"%{q_norm}%"
        where.append(or_(NewsArticle.raw_title.ilike(like), NewsArticle.source.ilike(like)))
    if filter_norm == "liquidation":
        where.append(NewsArticle.is_liquidation.is_(True))
    elif filter_norm == "news":
        where.append(NewsArticle.is_liquidation.is_(False))

    total_stmt = select(func.count(NewsArticle.id))
    if where:
        total_stmt = total_stmt.where(*where)
    total = int((await db.execute(total_stmt)).scalar() or 0)

    translations_count = (
        select(func.count(NewsArticleTranslation.id))
        .where(NewsArticleTranslation.article_id == NewsArticle.id)
        .correlate(NewsArticle)
        .scalar_subquery()
    )

    stmt = (
        select(
            NewsArticle.id,
            NewsArticle.source,
            NewsArticle.url,
            NewsArticle.raw_title,
            NewsArticle.is_liquidation,
            NewsArticle.published_at,
            NewsArticle.released_at,
            translations_count.label("translations"),
        )
        .order_by(NewsArticle.published_at.desc().nullslast(), NewsArticle.id.desc())
        .limit(limit)
        .offset(offset)
    )
    if where:
        stmt = stmt.where(*where)

    rows = (await db.execute(stmt)).all()
    items = [
        AdminNewsArticleItem(
            id=int(r.id),
            source=r.source,
            url=r.url,
            raw_title=r.raw_title,
            is_liquidation=bool(r.is_liquidation),
            published_at=r.published_at,
            released_at=r.released_at,
            translations=int(r.translations or 0),
        )
        for r in rows
    ]
    return AdminNewsArticleListResponse(items=items, total=total)


@router.delete(
    "/news-articles/{article_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
)
async def delete_admin_news_article(
    article_id: int,
    _: str = Depends(_require_admin),
    db: AsyncSession = Depends(get_db),
) -> Response:
    article = await db.get(NewsArticle, int(article_id))
    if article is None:
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    await db.delete(article)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/notifications", response_model=AdminNotificationListResponse)
async def list_admin_notifications(
    user_id: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    _: str = Depends(_require_admin),
    db: AsyncSession = Depends(get_db),
) -> AdminNotificationListResponse:
    where = []
    if user_id and user_id.strip():
        where.append(Notification.user_id == user_id.strip())

    total_stmt = select(func.count(Notification.id))
    if where:
        total_stmt = total_stmt.where(*where)
    total = int((await db.execute(total_stmt)).scalar() or 0)

    stmt = (
        select(
            Notification.id,
            Notification.user_id,
            User.username,
            Notification.event_type,
            Notification.is_read,
            Notification.created_at,
        )
        .select_from(Notification)
        .join(User, User.id == Notification.user_id)
        .order_by(Notification.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    if where:
        stmt = stmt.where(*where)
    rows = (await db.execute(stmt)).all()

    items = [
        AdminNotificationItem(
            id=r.id,
            user_id=r.user_id,
            username=r.username,
            event_type=r.event_type,
            is_read=bool(r.is_read),
            created_at=r.created_at,
        )
        for r in rows
    ]
    return AdminNotificationListResponse(items=items, total=total)


@router.delete(
    "/notifications/{notification_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
)
async def delete_admin_notification(
    notification_id: str,
    _: str = Depends(_require_admin),
    db: AsyncSession = Depends(get_db),
) -> Response:
    nid = notification_id.strip()
    if not nid:
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    notif = await db.get(Notification, nid)
    if notif is None:
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    await db.delete(notif)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


def _serialize_ai_provider_config(cfg: AiProviderConfig) -> AiProviderConfigOut:
    return AiProviderConfigOut(
        id=cfg.id,
        provider=cfg.provider,
        label=(cfg.label or "").strip(),
        model=cfg.model,
        sort_order=max(1, int(cfg.sort_order or 1)),
        enabled=bool(cfg.enabled),
        updated_at=cfg.updated_at,
        has_api_key=bool((cfg.api_key or "").strip()),
        api_key_hint=mask_api_key(cfg.api_key),
    )


@router.get("/ai-provider-configs", response_model=list[AiProviderConfigOut])
async def list_ai_provider_configs(
    _: str = Depends(_require_admin),
    db: AsyncSession = Depends(get_db),
) -> list[AiProviderConfigOut]:
    items = (
        await db.scalars(
            select(AiProviderConfig)
            .where(AiProviderConfig.provider == "gemini")
            .order_by(AiProviderConfig.sort_order.asc(), AiProviderConfig.updated_at.desc())
        )
    ).all()
    return [_serialize_ai_provider_config(i) for i in items]


@router.post(
    "/ai-provider-configs",
    response_model=AiProviderConfigOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_ai_provider_config(
    payload: AiProviderConfigCreate,
    _: str = Depends(_require_admin),
    db: AsyncSession = Depends(get_db),
) -> AiProviderConfigOut:
    if await count_gemini_config_rows(db) >= MAX_GEMINI_API_KEYS:
        raise HTTPException(
            status_code=400,
            detail=f"You can store up to {MAX_GEMINI_API_KEYS} Gemini API keys.",
        )

    api_key = payload.api_key.strip()
    if not api_key:
        raise HTTPException(status_code=400, detail="API key is required.")

    cfg = AiProviderConfig(
        provider="gemini",
        label=(payload.label or "").strip(),
        api_key=api_key,
        model=(payload.model or "").strip() or DEFAULT_GEMINI_MODEL,
        enabled=bool(payload.enabled),
        sort_order=int(payload.sort_order or 1),
    )
    db.add(cfg)
    await place_gemini_config(db, cfg, desired_order=cfg.sort_order)
    await db.commit()
    await db.refresh(cfg)
    return _serialize_ai_provider_config(cfg)


@router.put("/ai-provider-configs/{config_id}", response_model=AiProviderConfigOut)
async def update_ai_provider_config(
    config_id: int,
    payload: AiProviderConfigPatch,
    _: str = Depends(_require_admin),
    db: AsyncSession = Depends(get_db),
) -> AiProviderConfigOut:
    cfg = await db.get(AiProviderConfig, config_id)
    if cfg is None or cfg.provider != "gemini":
        raise HTTPException(status_code=404, detail="Config not found.")

    if payload.label is not None:
        cfg.label = payload.label.strip()
    if payload.api_key is not None:
        cfg.api_key = payload.api_key.strip() or None
    if payload.model is not None:
        cfg.model = payload.model.strip() or cfg.model
    if payload.enabled is not None:
        cfg.enabled = bool(payload.enabled)
    if payload.sort_order is not None:
        cfg.sort_order = int(payload.sort_order)

    await place_gemini_config(db, cfg, desired_order=cfg.sort_order)
    await db.commit()
    await db.refresh(cfg)
    return _serialize_ai_provider_config(cfg)


@router.delete(
    "/ai-provider-configs/{config_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
)
async def delete_ai_provider_config(
    config_id: int,
    _: str = Depends(_require_admin),
    db: AsyncSession = Depends(get_db),
) -> Response:
    cfg = await db.get(AiProviderConfig, config_id)
    if cfg is None or cfg.provider != "gemini":
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    await db.delete(cfg)
    await db.flush()
    await rebalance_gemini_config_rows(db)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)

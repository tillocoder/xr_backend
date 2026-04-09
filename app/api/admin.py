from __future__ import annotations

import secrets
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
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
    Comment,
    CommentReaction,
    CommunityFollow,
    NewsArticle,
    Notification,
    Post,
    PostReaction,
    PollVote,
    PushToken,
    User,
)
from app.presentation.api.request_state import get_auth_session_service
from app.schemas.admin_dashboard import AdminOverviewResponse, AdminStatsResponse
from app.services.daily_reward_service import DailyRewardService
from app.services.rank_theme import coerce_rank_theme_for_membership
from app.services.ai_provider_config_service import (
    DEFAULT_GEMINI_MODEL,
    GEMINI_SCOPE_DEFAULT,
    MAX_GEMINI_API_KEYS,
    count_gemini_config_rows,
    mask_api_key,
    normalize_gemini_usage_scope,
    place_gemini_config,
    rebalance_gemini_config_rows,
)
from app.services.admin_dashboard_service import AdminDashboardService
from app.services.user_service import ensure_user_exists

router = APIRouter(prefix="/admin", tags=["admin"])

_security = HTTPBasic()
_admin_dashboard_service = AdminDashboardService()
_daily_rewards = DailyRewardService()


def _require_admin(credentials: HTTPBasicCredentials = Depends(_security)) -> str:
    settings = get_settings()
    if not settings.admin_features_enabled:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found.")
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


class AdminUserItem(BaseModel):
    id: str
    username: str
    display_name: str
    membership_tier: str
    is_pro: bool
    rank_theme: str | None = None
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
    rank_theme: str | None = None
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


class AdminIssueUserSessionRequest(BaseModel):
    userId: str | None = None
    displayName: str | None = None
    isPro: bool = False


class AdminIssuedUserSessionUser(BaseModel):
    id: str
    displayName: str
    isPro: bool


class AdminIssuedUserSessionResponse(BaseModel):
    accessToken: str
    refreshToken: str
    authorizationHeader: str
    accessExpiresAt: datetime
    refreshExpiresAt: datetime
    user: AdminIssuedUserSessionUser


class AiProviderConfigCreate(BaseModel):
    usage_scope: str = GEMINI_SCOPE_DEFAULT
    label: str | None = None
    api_key: str
    model: str = DEFAULT_GEMINI_MODEL
    enabled: bool = True
    sort_order: int = Field(default=1, ge=1, le=MAX_GEMINI_API_KEYS)


class AiProviderConfigPatch(BaseModel):
    usage_scope: str | None = None
    label: str | None = None
    api_key: str | None = None
    model: str | None = None
    enabled: bool | None = None
    sort_order: int | None = Field(default=None, ge=1, le=MAX_GEMINI_API_KEYS)


class AiProviderConfigOut(BaseModel):
    id: int
    provider: str
    usage_scope: str
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
    return await _admin_dashboard_service.get_stats(db)


@router.get("/overview", response_model=AdminOverviewResponse)
async def get_admin_overview(
    days: int = Query(default=30, ge=1, le=90),
    _: str = Depends(_require_admin),
    db: AsyncSession = Depends(get_db),
) -> AdminOverviewResponse:
    return await _admin_dashboard_service.get_overview(db, days=days)


@router.post("/issue-user-session", response_model=AdminIssuedUserSessionResponse)
async def issue_admin_user_session(
    payload: AdminIssueUserSessionRequest,
    request: Request,
    _: str = Depends(_require_admin),
    db: AsyncSession = Depends(get_db),
) -> AdminIssuedUserSessionResponse:
    user_id = (payload.userId or "").strip() or f"swagger_{secrets.token_hex(10)}"
    display_name = (payload.displayName or "").strip() or "Swagger Test User"
    user = await ensure_user_exists(
        db,
        user_id,
        display_name=display_name,
        is_pro=payload.isPro,
    )

    changed = False
    if user.display_name != display_name:
        user.display_name = display_name
        changed = True
    if bool(user.is_pro) != bool(payload.isPro):
        user.is_pro = bool(payload.isPro)
        user.membership_tier = "pro" if user.is_pro else "free"
        changed = True
    if changed:
        await db.flush()

    session = await get_auth_session_service(request).issue_session(db, user_id=user.id)
    return AdminIssuedUserSessionResponse(
        accessToken=session.access_token,
        refreshToken=session.refresh_token,
        authorizationHeader=f"Bearer {session.access_token}",
        accessExpiresAt=session.access_expires_at,
        refreshExpiresAt=session.refresh_expires_at,
        user=AdminIssuedUserSessionUser(
            id=user.id,
            displayName=user.display_name,
            isPro=user.is_pro,
        ),
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
            User.rank_theme,
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
            rank_theme=(row.rank_theme or "").strip() or None,
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
    if payload.rank_theme is not None:
        effective_membership_tier = _daily_rewards.effective_membership_tier_user(
            user,
        )
        normalized_rank_theme = coerce_rank_theme_for_membership(
            value=payload.rank_theme,
            membership_tier=effective_membership_tier,
        )
        user.rank_theme = None if normalized_rank_theme == "classic" else normalized_rank_theme
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
        rank_theme=(user.rank_theme or "").strip() or None,
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
        usage_scope=normalize_gemini_usage_scope(cfg.usage_scope),
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
            .order_by(
                AiProviderConfig.usage_scope.asc(),
                AiProviderConfig.sort_order.asc(),
                AiProviderConfig.updated_at.desc(),
            )
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
    usage_scope = normalize_gemini_usage_scope(payload.usage_scope)
    if await count_gemini_config_rows(db, usage_scope=usage_scope) >= MAX_GEMINI_API_KEYS:
        raise HTTPException(
            status_code=400,
            detail=f"You can store up to {MAX_GEMINI_API_KEYS} Gemini API keys per usage.",
        )

    api_key = payload.api_key.strip()
    if not api_key:
        raise HTTPException(status_code=400, detail="API key is required.")

    cfg = AiProviderConfig(
        provider="gemini",
        usage_scope=usage_scope,
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

    previous_scope = normalize_gemini_usage_scope(cfg.usage_scope)
    next_scope = previous_scope
    if payload.usage_scope is not None:
        next_scope = normalize_gemini_usage_scope(payload.usage_scope)
    if (
        next_scope != previous_scope
        and await count_gemini_config_rows(
            db,
            usage_scope=next_scope,
            exclude_id=cfg.id,
        )
        >= MAX_GEMINI_API_KEYS
    ):
        raise HTTPException(
            status_code=400,
            detail=f"You can store up to {MAX_GEMINI_API_KEYS} Gemini API keys per usage.",
        )

    cfg.usage_scope = next_scope
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
    if previous_scope != cfg.usage_scope:
        await rebalance_gemini_config_rows(db, usage_scope=previous_scope)
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

    previous_scope = normalize_gemini_usage_scope(cfg.usage_scope)
    await db.delete(cfg)
    await db.flush()
    await rebalance_gemini_config_rows(db, usage_scope=previous_scope)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)

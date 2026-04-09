from __future__ import annotations

import secrets

import anyio
from fastapi import FastAPI
from sqlalchemy import create_engine, delete, func, select, update
from sqladmin import Admin, ModelView
from sqladmin.authentication import AuthenticationBackend
from sqladmin.fields import SelectField
from starlette.requests import Request

from app.core.config import get_settings
from app.models.entities import (
    AiProviderConfig,
    AuthSession,
    Chat,
    ChatMember,
    Comment,
    CommentReaction,
    CommunityFollow,
    CommunityProfile,
    LearningVideoLesson,
    Message,
    Notification,
    PollVote,
    Post,
    PostReaction,
    PostView,
    PushToken,
    User,
    UserTargetAlert,
    NewsArticle,
    NewsArticleTranslation,
)
from app.services.ai_provider_config_service import (
    DEFAULT_GEMINI_MODEL,
    GEMINI_SCOPE_DEFAULT,
    GEMINI_SCOPE_PORTFOLIO,
    MAX_GEMINI_API_KEYS,
    mask_api_key,
    normalize_gemini_usage_scope,
)


def _to_sync_database_url(database_url: str) -> str:
    # The app uses an async engine; the admin panel uses a sync engine for compatibility.
    if database_url.startswith("postgresql+asyncpg://"):
        return "postgresql+psycopg://" + database_url.removeprefix("postgresql+asyncpg://")
    return database_url


class _AdminAuth(AuthenticationBackend):
    async def login(self, request: Request) -> bool:
        form = await request.form()
        username = str(form.get("username") or "")
        password = str(form.get("password") or "")

        settings = get_settings()
        ok = secrets.compare_digest(username, settings.admin_panel_username) and secrets.compare_digest(
            password, settings.admin_panel_password
        )
        if ok:
            request.session.update({"admin": True})
        return ok

    async def logout(self, request: Request) -> bool:
        request.session.clear()
        return True

    async def authenticate(self, request: Request) -> bool:
        return bool(request.session.get("admin"))


class UserAdmin(ModelView, model=User):
    column_list = [
        User.id,
        User.username,
        User.display_name,
        User.membership_tier,
        User.is_pro,
        User.diamonds_balance,
        User.daily_reward_streak,
        User.created_at,
        User.updated_at,
    ]
    column_searchable_list = [User.id, User.username, User.display_name]
    column_default_sort = (User.created_at, True)
    form_excluded_columns = [User.created_at, User.updated_at]

    async def delete_model(self, request: Request, pk: str) -> None:
        await anyio.to_thread.run_sync(self._delete_model_sync, pk)

    def _delete_model_sync(self, pk: str) -> None:
        with self.session_maker(expire_on_commit=False) as session:
            user = session.execute(self._stmt_by_identifier(pk)).scalars().first()
            if user is None:
                return
            self._delete_dependencies_sync(session, user.id)
            session.delete(user)
            session.commit()

    def _delete_dependencies_sync(self, session, user_id: str) -> None:
        authored_post_ids = list(
            session.execute(select(Post.id).where(Post.author_id == user_id)).scalars().all()
        )
        if authored_post_ids:
            post_comment_ids = list(
                session.execute(select(Comment.id).where(Comment.post_id.in_(authored_post_ids))).scalars().all()
            )
            if post_comment_ids:
                session.execute(
                    update(Comment)
                    .where(Comment.reply_to_comment_id.in_(post_comment_ids))
                    .values(reply_to_comment_id=None)
                )
                session.execute(
                    delete(CommentReaction).where(CommentReaction.comment_id.in_(post_comment_ids))
                )
                session.execute(delete(Comment).where(Comment.id.in_(post_comment_ids)))
            session.execute(delete(PollVote).where(PollVote.post_id.in_(authored_post_ids)))
            session.execute(delete(PostView).where(PostView.post_id.in_(authored_post_ids)))
            session.execute(delete(PostReaction).where(PostReaction.post_id.in_(authored_post_ids)))
            session.execute(delete(Post).where(Post.id.in_(authored_post_ids)))

        remaining_comment_stmt = select(Comment.id, Comment.post_id).where(Comment.author_id == user_id)
        if authored_post_ids:
            remaining_comment_stmt = remaining_comment_stmt.where(~Comment.post_id.in_(authored_post_ids))
        authored_comments = list(session.execute(remaining_comment_stmt).all())
        if authored_comments:
            authored_comment_ids = [str(comment_id) for comment_id, _ in authored_comments]
            affected_post_ids = sorted({str(post_id) for _, post_id in authored_comments})
            session.execute(
                update(Comment)
                .where(Comment.reply_to_comment_id.in_(authored_comment_ids))
                .values(reply_to_comment_id=None)
            )
            session.execute(
                delete(CommentReaction).where(CommentReaction.comment_id.in_(authored_comment_ids))
            )
            session.execute(delete(Comment).where(Comment.id.in_(authored_comment_ids)))
            for post_id in affected_post_ids:
                remaining_count = int(
                    session.execute(
                        select(func.count(Comment.id)).where(Comment.post_id == post_id)
                    ).scalar()
                    or 0
                )
                session.execute(
                    update(Post)
                    .where(Post.id == post_id)
                    .values(comment_count=remaining_count)
                )

        authored_message_ids = list(
            session.execute(select(Message.id).where(Message.sender_id == user_id)).scalars().all()
        )
        if authored_message_ids:
            session.execute(
                update(Message)
                .where(Message.reply_to_message_id.in_(authored_message_ids))
                .values(reply_to_message_id=None)
            )
            session.execute(
                update(Chat)
                .where(Chat.last_message_id.in_(authored_message_ids))
                .values(last_message_id=None)
            )
            session.execute(
                update(ChatMember)
                .where(ChatMember.last_read_message_id.in_(authored_message_ids))
                .values(last_read_message_id=None)
            )
            session.execute(delete(Message).where(Message.id.in_(authored_message_ids)))

        session.execute(delete(ChatMember).where(ChatMember.user_id == user_id))
        session.execute(delete(CommunityFollow).where(CommunityFollow.follower_uid == user_id))
        session.execute(delete(CommunityFollow).where(CommunityFollow.following_uid == user_id))
        session.execute(delete(PostReaction).where(PostReaction.user_id == user_id))
        session.execute(delete(PostView).where(PostView.user_id == user_id))
        session.execute(delete(PollVote).where(PollVote.user_id == user_id))
        session.execute(delete(CommentReaction).where(CommentReaction.user_id == user_id))
        session.execute(delete(Notification).where(Notification.user_id == user_id))
        session.execute(delete(PushToken).where(PushToken.user_id == user_id))
        session.execute(delete(AuthSession).where(AuthSession.user_id == user_id))
        session.execute(delete(CommunityProfile).where(CommunityProfile.uid == user_id))
        session.execute(delete(UserTargetAlert).where(UserTargetAlert.user_id == user_id))


class CommunityProfileAdmin(ModelView, model=CommunityProfile):
    column_list = [
        CommunityProfile.uid,
        CommunityProfile.username,
        CommunityProfile.display_name,
        CommunityProfile.is_pro,
        CommunityProfile.created_at,
        CommunityProfile.updated_at,
    ]
    column_searchable_list = [CommunityProfile.uid, CommunityProfile.username, CommunityProfile.display_name]
    column_default_sort = (CommunityProfile.updated_at, True)
    form_excluded_columns = [CommunityProfile.created_at, CommunityProfile.updated_at]


class _SafeDeleteModelView(ModelView):
    async def delete_model(self, request: Request, pk: str) -> None:
        await anyio.to_thread.run_sync(self._delete_model_sync, pk)

    def _delete_model_sync(self, pk: str) -> None:
        with self.session_maker(expire_on_commit=False) as session:
            obj = session.execute(self._stmt_by_identifier(pk)).scalars().first()
            if obj is None:
                return
            self._delete_dependencies_sync(session, obj)
            session.delete(obj)
            session.commit()

    def _delete_dependencies_sync(self, session, obj) -> None:
        return None


class PostAdmin(_SafeDeleteModelView, model=Post):
    column_list = [
        Post.id,
        Post.author_id,
        Post.symbol,
        Post.market_bias,
        Post.comment_count,
        Post.view_count,
        Post.created_at,
    ]
    column_searchable_list = [Post.id, Post.author_id, Post.content]
    column_default_sort = (Post.created_at, True)

    def _delete_dependencies_sync(self, session, obj: Post) -> None:
        post_id = obj.id
        comment_ids = list(
            session.execute(
                select(Comment.id).where(Comment.post_id == post_id)
            ).scalars().all()
        )
        if comment_ids:
            session.execute(
                update(Comment)
                .where(Comment.reply_to_comment_id.in_(comment_ids))
                .values(reply_to_comment_id=None)
            )
            session.execute(
                delete(CommentReaction).where(CommentReaction.comment_id.in_(comment_ids))
            )
            session.execute(delete(Comment).where(Comment.id.in_(comment_ids)))
        session.execute(delete(PollVote).where(PollVote.post_id == post_id))
        session.execute(delete(PostView).where(PostView.post_id == post_id))
        session.execute(delete(PostReaction).where(PostReaction.post_id == post_id))


class CommentAdmin(_SafeDeleteModelView, model=Comment):
    column_list = [
        Comment.id,
        Comment.post_id,
        Comment.author_id,
        Comment.reply_to_comment_id,
        Comment.created_at,
    ]
    column_searchable_list = [Comment.id, Comment.post_id, Comment.author_id, Comment.content]
    column_default_sort = (Comment.created_at, True)

    def _delete_dependencies_sync(self, session, obj: Comment) -> None:
        session.execute(
            update(Comment)
            .where(Comment.reply_to_comment_id == obj.id)
            .values(reply_to_comment_id=None)
        )
        session.execute(delete(CommentReaction).where(CommentReaction.comment_id == obj.id))
        session.execute(
            update(Post)
            .where(Post.id == obj.post_id, Post.comment_count > 0)
            .values(comment_count=Post.comment_count - 1)
        )


class MessageAdmin(_SafeDeleteModelView, model=Message):
    column_list = [
        Message.id,
        Message.chat_id,
        Message.sender_id,
        Message.message_type,
        Message.reply_to_message_id,
        Message.deleted_at,
        Message.created_at,
    ]
    column_searchable_list = [Message.id, Message.chat_id, Message.sender_id, Message.body]
    column_default_sort = (Message.created_at, True)

    def _delete_dependencies_sync(self, session, obj: Message) -> None:
        session.execute(
            update(Message)
            .where(Message.reply_to_message_id == obj.id)
            .values(reply_to_message_id=None)
        )
        session.execute(
            update(Chat)
            .where(Chat.last_message_id == obj.id)
            .values(last_message_id=None)
        )
        session.execute(
            update(ChatMember)
            .where(ChatMember.last_read_message_id == obj.id)
            .values(last_read_message_id=None)
        )


class ChatAdmin(_SafeDeleteModelView, model=Chat):
    column_list = [Chat.id, Chat.chat_type, Chat.last_message_id, Chat.created_at]
    column_searchable_list = [Chat.id, Chat.chat_type]
    column_default_sort = (Chat.created_at, True)

    def _delete_dependencies_sync(self, session, obj: Chat) -> None:
        session.execute(
            update(ChatMember)
            .where(ChatMember.chat_id == obj.id)
            .values(last_read_message_id=None, unread_count=0)
        )
        session.execute(
            update(Message)
            .where(Message.chat_id == obj.id)
            .values(reply_to_message_id=None)
        )
        session.execute(delete(ChatMember).where(ChatMember.chat_id == obj.id))
        session.execute(delete(Message).where(Message.chat_id == obj.id))


class ChatMemberAdmin(ModelView, model=ChatMember):
    column_list = [
        ChatMember.id,
        ChatMember.chat_id,
        ChatMember.user_id,
        ChatMember.unread_count,
        ChatMember.joined_at,
    ]
    column_searchable_list = [ChatMember.id, ChatMember.chat_id, ChatMember.user_id]
    column_default_sort = (ChatMember.joined_at, True)


class NotificationAdmin(ModelView, model=Notification):
    column_list = [Notification.id, Notification.user_id, Notification.event_type, Notification.is_read, Notification.created_at]
    column_searchable_list = [Notification.id, Notification.user_id, Notification.event_type]
    column_default_sort = (Notification.created_at, True)


class PushTokenAdmin(ModelView, model=PushToken):
    column_list = [PushToken.token, PushToken.user_id, PushToken.platform, PushToken.updated_at]
    column_searchable_list = [PushToken.token, PushToken.user_id]
    column_default_sort = (PushToken.updated_at, True)


class LearningVideoLessonAdmin(ModelView, model=LearningVideoLesson):
    column_list = [
        LearningVideoLesson.id,
        LearningVideoLesson.title,
        LearningVideoLesson.tag_key,
        LearningVideoLesson.is_published,
        LearningVideoLesson.is_featured,
        LearningVideoLesson.sort_order,
        LearningVideoLesson.updated_at,
    ]
    column_searchable_list = [LearningVideoLesson.id, LearningVideoLesson.title, LearningVideoLesson.summary]
    column_default_sort = (LearningVideoLesson.updated_at, True)


class PostReactionAdmin(ModelView, model=PostReaction):
    column_list = [PostReaction.id, PostReaction.post_id, PostReaction.user_id, PostReaction.reaction_type, PostReaction.created_at]
    column_searchable_list = [PostReaction.post_id, PostReaction.user_id]
    column_default_sort = (PostReaction.created_at, True)


class PostViewAdmin(ModelView, model=PostView):
    column_list = [PostView.id, PostView.post_id, PostView.user_id, PostView.created_at]
    column_searchable_list = [PostView.post_id, PostView.user_id]
    column_default_sort = (PostView.created_at, True)


class PollVoteAdmin(ModelView, model=PollVote):
    column_list = [PollVote.id, PollVote.post_id, PollVote.user_id, PollVote.option_index, PollVote.created_at]
    column_searchable_list = [PollVote.post_id, PollVote.user_id]
    column_default_sort = (PollVote.created_at, True)


class CommentReactionAdmin(ModelView, model=CommentReaction):
    column_list = [CommentReaction.id, CommentReaction.comment_id, CommentReaction.user_id, CommentReaction.reaction_code, CommentReaction.created_at]
    column_searchable_list = [CommentReaction.comment_id, CommentReaction.user_id]
    column_default_sort = (CommentReaction.created_at, True)


class CommunityFollowAdmin(ModelView, model=CommunityFollow):
    column_list = [CommunityFollow.id, CommunityFollow.follower_uid, CommunityFollow.following_uid, CommunityFollow.created_at]
    column_searchable_list = [CommunityFollow.follower_uid, CommunityFollow.following_uid]
    column_default_sort = (CommunityFollow.created_at, True)


class AuthSessionAdmin(ModelView, model=AuthSession):
    can_create = False
    can_edit = False
    column_list = [
        AuthSession.id,
        AuthSession.user_id,
        AuthSession.access_expires_at,
        AuthSession.refresh_expires_at,
        AuthSession.created_at,
        AuthSession.last_seen_at,
    ]
    column_details_list = [
        AuthSession.id,
        AuthSession.user_id,
        AuthSession.access_expires_at,
        AuthSession.refresh_expires_at,
        AuthSession.created_at,
        AuthSession.last_seen_at,
    ]
    column_searchable_list = [AuthSession.id, AuthSession.user_id]
    column_default_sort = (AuthSession.last_seen_at, True)


class AiProviderConfigAdmin(ModelView, model=AiProviderConfig):
    name = "Gemini API Key"
    name_plural = "Gemini API Keys"
    column_list = [
        AiProviderConfig.id,
        AiProviderConfig.usage_scope,
        AiProviderConfig.label,
        AiProviderConfig.sort_order,
        AiProviderConfig.api_key,
        AiProviderConfig.model,
        AiProviderConfig.enabled,
        AiProviderConfig.updated_at,
    ]
    column_labels = {
        AiProviderConfig.usage_scope: "Usage",
        AiProviderConfig.label: "Label",
        AiProviderConfig.sort_order: "Priority",
        AiProviderConfig.api_key: "API Key",
        AiProviderConfig.model: "Model",
        AiProviderConfig.enabled: "Enabled",
        AiProviderConfig.updated_at: "Updated",
    }
    column_formatters = {
        AiProviderConfig.api_key: lambda m, a: mask_api_key(m.api_key),
    }
    column_searchable_list = [
        AiProviderConfig.usage_scope,
        AiProviderConfig.label,
        AiProviderConfig.model,
    ]
    column_default_sort = [
        (AiProviderConfig.usage_scope, False),
        (AiProviderConfig.sort_order, False),
        (AiProviderConfig.updated_at, True),
    ]
    form_columns = [
        AiProviderConfig.usage_scope,
        AiProviderConfig.label,
        AiProviderConfig.api_key,
        AiProviderConfig.model,
        AiProviderConfig.sort_order,
        AiProviderConfig.enabled,
    ]
    form_overrides = {
        "usage_scope": SelectField,
    }
    form_args = {
        "usage_scope": {
            "choices": [
                (GEMINI_SCOPE_DEFAULT, "Default"),
                (GEMINI_SCOPE_PORTFOLIO, "Portfolio"),
            ],
        },
    }

    async def on_model_change(self, data, model, is_created: bool, request: Request) -> None:
        usage_scope = normalize_gemini_usage_scope(
            data.get("usage_scope") or getattr(model, "usage_scope", GEMINI_SCOPE_DEFAULT)
        )
        data["usage_scope"] = usage_scope
        data["provider"] = "gemini"
        data["model"] = str(data.get("model") or model.model or DEFAULT_GEMINI_MODEL).strip() or DEFAULT_GEMINI_MODEL
        data["sort_order"] = max(1, min(int(data.get("sort_order") or model.sort_order or 1), MAX_GEMINI_API_KEYS))
        label = str(data.get("label") or model.label or "").strip()
        data["label"] = label or f"Gemini Key {data['sort_order']}"
        api_key = str(data.get("api_key") or model.api_key or "").strip()
        if is_created and not api_key:
            raise ValueError("API key is required.")

        previous_scope = usage_scope
        if not is_created and getattr(model, "id", None):
            with self.session_maker(expire_on_commit=False) as session:
                existing = session.get(AiProviderConfig, int(model.id))
                if existing is not None:
                    previous_scope = normalize_gemini_usage_scope(existing.usage_scope)
        request.state.ai_provider_previous_usage_scope = previous_scope

        if is_created:
            with self.session_maker(expire_on_commit=False) as session:
                total = self._count_scope_rows_sync(session, usage_scope=usage_scope)
                if total >= MAX_GEMINI_API_KEYS:
                    raise ValueError(
                        f"You can store up to {MAX_GEMINI_API_KEYS} Gemini API keys per usage."
                    )
            return

        with self.session_maker(expire_on_commit=False) as session:
            total = self._count_scope_rows_sync(
                session,
                usage_scope=usage_scope,
                exclude_id=int(model.id),
            )
            if total >= MAX_GEMINI_API_KEYS:
                raise ValueError(
                    f"You can store up to {MAX_GEMINI_API_KEYS} Gemini API keys per usage."
                )

    async def after_model_change(self, data, model, is_created: bool, request: Request) -> None:
        with self.session_maker(expire_on_commit=False) as session:
            current = session.get(AiProviderConfig, int(model.id))
            if current is None:
                return
            current_scope = normalize_gemini_usage_scope(current.usage_scope)
            previous_scope = normalize_gemini_usage_scope(
                getattr(request.state, "ai_provider_previous_usage_scope", current_scope)
            )
            self._place_scope_row_sync(session, current)
            if previous_scope != current_scope:
                self._rebalance_scope_rows_sync(session, previous_scope)
            session.commit()

    async def after_model_delete(self, model, request: Request) -> None:
        with self.session_maker(expire_on_commit=False) as session:
            self._rebalance_scope_rows_sync(
                session,
                normalize_gemini_usage_scope(getattr(model, "usage_scope", GEMINI_SCOPE_DEFAULT)),
            )
            session.commit()

    def _count_scope_rows_sync(self, session, *, usage_scope: str, exclude_id: int | None = None) -> int:
        stmt = select(func.count(AiProviderConfig.id)).where(
            AiProviderConfig.provider == "gemini",
            AiProviderConfig.usage_scope == usage_scope,
        )
        if exclude_id is not None:
            stmt = stmt.where(AiProviderConfig.id != int(exclude_id))
        return int(session.execute(stmt).scalar() or 0)

    def _scope_rows_sync(self, session, usage_scope: str) -> list[AiProviderConfig]:
        return list(
            session.execute(
                select(AiProviderConfig)
                .where(
                    AiProviderConfig.provider == "gemini",
                    AiProviderConfig.usage_scope == usage_scope,
                )
                .order_by(AiProviderConfig.sort_order.asc(), AiProviderConfig.id.asc())
            ).scalars().all()
        )

    def _place_scope_row_sync(self, session, row: AiProviderConfig) -> None:
        usage_scope = normalize_gemini_usage_scope(row.usage_scope)
        row.usage_scope = usage_scope
        rows = [item for item in self._scope_rows_sync(session, usage_scope) if item.id != row.id]
        target = max(1, min(int(row.sort_order or 1), len(rows) + 1))
        rows.insert(target - 1, row)
        for index, item in enumerate(rows, start=1):
            item.sort_order = index
            item.label = str(item.label or "").strip() or f"Gemini Key {index}"

    def _rebalance_scope_rows_sync(self, session, usage_scope: str) -> None:
        rows = self._scope_rows_sync(session, usage_scope)
        for index, row in enumerate(rows, start=1):
            row.sort_order = index
            row.label = str(row.label or "").strip() or f"Gemini Key {index}"


class NewsArticleAdmin(ModelView, model=NewsArticle):
    column_list = [
        NewsArticle.id,
        NewsArticle.source,
        NewsArticle.category,
        NewsArticle.view_count,
        NewsArticle.is_liquidation,
        NewsArticle.published_at,
        NewsArticle.released_at,
        NewsArticle.notified_at,
        NewsArticle.raw_title,
        NewsArticle.url,
        NewsArticle.created_at,
    ]
    column_searchable_list = [NewsArticle.raw_title, NewsArticle.url, NewsArticle.source]
    column_default_sort = (NewsArticle.published_at, True)


class NewsArticleTranslationAdmin(ModelView, model=NewsArticleTranslation):
    column_list = [
        NewsArticleTranslation.id,
        NewsArticleTranslation.article_id,
        NewsArticleTranslation.lang,
        NewsArticleTranslation.title,
        NewsArticleTranslation.model,
        NewsArticleTranslation.created_at,
    ]
    column_searchable_list = [NewsArticleTranslation.lang, NewsArticleTranslation.title]
    column_default_sort = (NewsArticleTranslation.created_at, True)


def setup_admin_panel(app: FastAPI) -> Admin:
    settings = get_settings()
    if not settings.admin_features_enabled:
        raise RuntimeError("Admin panel is disabled or configured with insecure credentials.")
    engine = create_engine(_to_sync_database_url(settings.database_url), pool_pre_ping=True)

    auth_backend = _AdminAuth(secret_key=settings.admin_panel_secret_key)
    admin = Admin(
        app,
        engine=engine,
        authentication_backend=auth_backend,
        base_url="/admin-panel",
        title="XR Admin",
    )

    admin.add_view(UserAdmin)
    admin.add_view(CommunityProfileAdmin)
    admin.add_view(PostAdmin)
    admin.add_view(CommentAdmin)
    admin.add_view(MessageAdmin)
    admin.add_view(ChatAdmin)
    admin.add_view(ChatMemberAdmin)
    admin.add_view(NotificationAdmin)
    admin.add_view(PushTokenAdmin)
    admin.add_view(LearningVideoLessonAdmin)
    admin.add_view(PostReactionAdmin)
    admin.add_view(PostViewAdmin)
    admin.add_view(PollVoteAdmin)
    admin.add_view(CommentReactionAdmin)
    admin.add_view(CommunityFollowAdmin)
    admin.add_view(AuthSessionAdmin)
    admin.add_view(AiProviderConfigAdmin)
    admin.add_view(NewsArticleAdmin)
    admin.add_view(NewsArticleTranslationAdmin)

    return admin

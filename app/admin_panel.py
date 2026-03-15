from __future__ import annotations

import secrets

from fastapi import FastAPI
from sqlalchemy import create_engine
from sqladmin import Admin, ModelView
from sqladmin.authentication import AuthenticationBackend
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
    NewsArticle,
    NewsArticleTranslation,
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


class PostAdmin(ModelView, model=Post):
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


class CommentAdmin(ModelView, model=Comment):
    column_list = [
        Comment.id,
        Comment.post_id,
        Comment.author_id,
        Comment.reply_to_comment_id,
        Comment.created_at,
    ]
    column_searchable_list = [Comment.id, Comment.post_id, Comment.author_id, Comment.content]
    column_default_sort = (Comment.created_at, True)


class MessageAdmin(ModelView, model=Message):
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


class ChatAdmin(ModelView, model=Chat):
    column_list = [Chat.id, Chat.chat_type, Chat.last_message_id, Chat.created_at]
    column_searchable_list = [Chat.id, Chat.chat_type]
    column_default_sort = (Chat.created_at, True)


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
    column_list = [
        AiProviderConfig.id,
        AiProviderConfig.provider,
        AiProviderConfig.model,
        AiProviderConfig.enabled,
        AiProviderConfig.updated_at,
    ]
    column_searchable_list = [AiProviderConfig.provider, AiProviderConfig.model]
    column_default_sort = (AiProviderConfig.updated_at, True)
    form_excluded_columns = [AiProviderConfig.created_at, AiProviderConfig.updated_at]


class NewsArticleAdmin(ModelView, model=NewsArticle):
    column_list = [
        NewsArticle.id,
        NewsArticle.source,
        NewsArticle.is_liquidation,
        NewsArticle.published_at,
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

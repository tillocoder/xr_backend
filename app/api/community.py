from fastapi import APIRouter, Depends, Query, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, get_bus, get_cache, get_current_user
from app.core.public_url import get_public_base_url_for_request
from app.db.session import get_db
from app.schemas.chat import (
    ChatConversationOut,
    ChatMessageCreate,
    ChatMessageOut,
    ChatMessageUpdate,
    ChatMessagesPage,
    ChatVoiceMessageCreate,
    UnreadCountOut,
)
from app.schemas.community import (
    CommunityAddCommentRequest,
    CommunityCommentResponse,
    CommunityCreatePostRequest,
    CommunityImageUploadRequest,
    CommunityImageUploadResponse,
    CommunityPostResponse,
    CommunityProfileResponse,
    CommunityProfileUpsertRequest,
    CommunityPublicHoldingSummaryResponse,
    CommunityReactCommentRequest,
    CommunityUpdatePostRequest,
    PostReactionChangeIn,
    PostReactionStateOut,
)
from app.schemas.feed import FeedPage
from app.services.cache import RedisCache
from app.services.chat_service import (
    delete_direct_chat_for_user,
    get_conversation_by_peer,
    get_unread_total,
    list_conversations,
    list_messages,
    list_messages_with_peer,
    mark_all_chats_read,
    mark_chat_read,
    mark_chat_read_with_peer,
    delete_message_for_user,
    send_message,
    send_message_to_peer,
    send_voice_message_to_peer,
    update_message_for_user,
)
from app.services.community_service import CommunityService
from app.services.feed_service import load_feed_page
from app.services.reaction_service import react_to_post
from app.ws.bus import RedisEventBus

router = APIRouter(prefix="/community", tags=["community"])


def _community_service(request: Request) -> CommunityService:
    return CommunityService(
        notification_service=request.app.state.notification_service,
        bus=request.app.state.bus,
        public_base_url=_public_base_url(request),
    )


def _public_base_url(request: Request) -> str:
    return get_public_base_url_for_request(request)


@router.get("/feed", response_model=FeedPage)
async def get_feed(
    cursor: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=50),
    user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    cache: RedisCache = Depends(get_cache),
) -> FeedPage:
    return await load_feed_page(db=db, cache=cache, viewer_id=user.id, cursor=cursor, limit=limit)


@router.get("/posts", response_model=list[CommunityPostResponse])
async def get_posts(
    request: Request,
    symbol: str | None = Query(default=None),
    limit: int = Query(default=15, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
) -> list[CommunityPostResponse]:
    return await _community_service(request).list_posts(db, symbol=symbol, limit=limit)


@router.get("/posts/{post_id}", response_model=CommunityPostResponse)
async def get_post(
    post_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> CommunityPostResponse:
    return await _community_service(request).get_post(db, post_id)


@router.get("/posts/{post_id}/comments", response_model=list[CommunityCommentResponse])
async def get_post_comments(
    post_id: str,
    request: Request,
    limit: int = Query(default=40, ge=1, le=120),
    db: AsyncSession = Depends(get_db),
) -> list[CommunityCommentResponse]:
    return await _community_service(request).list_comments(db, post_id=post_id, limit=limit)


@router.get("/posts/{post_id}/reaction")
async def get_post_reaction(
    post_id: str,
    request: Request,
    user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, str | None]:
    reaction = await _community_service(request).get_reaction(db, post_id=post_id, user_uid=user.id)
    return {"reactionKey": reaction}


@router.post("/posts", response_model=CommunityPostResponse, status_code=status.HTTP_201_CREATED)
async def create_post(
    payload: CommunityCreatePostRequest,
    request: Request,
    user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> CommunityPostResponse:
    return await _community_service(request).create_post(
        db,
        current_user_id=user.id,
        payload=payload,
    )


@router.put("/posts/{post_id}", response_model=CommunityPostResponse)
async def update_post(
    post_id: str,
    payload: CommunityUpdatePostRequest,
    request: Request,
    user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> CommunityPostResponse:
    return await _community_service(request).update_post(
        db,
        current_user_id=user.id,
        post_id=post_id,
        payload=payload,
    )


@router.delete("/posts/{post_id}", status_code=status.HTTP_204_NO_CONTENT, response_class=Response)
async def delete_post(
    post_id: str,
    request: Request,
    user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Response:
    await _community_service(request).delete_post(db, current_user_id=user.id, post_id=post_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/posts/{post_id}/views", status_code=status.HTTP_204_NO_CONTENT, response_class=Response)
async def register_post_view(
    post_id: str,
    request: Request,
    user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Response:
    await _community_service(request).register_post_view(db, current_user_id=user.id, post_id=post_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/posts/{post_id}/poll-votes", status_code=status.HTTP_204_NO_CONTENT, response_class=Response)
async def vote_on_poll(
    post_id: str,
    payload: dict[str, int],
    request: Request,
    user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Response:
    await _community_service(request).vote_on_poll(
        db,
        current_user_id=user.id,
        post_id=post_id,
        option_index=int(payload.get("optionIndex", -1)),
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/posts/{post_id}/comments", response_model=CommunityCommentResponse, status_code=status.HTTP_201_CREATED)
async def add_comment(
    post_id: str,
    payload: CommunityAddCommentRequest,
    request: Request,
    user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> CommunityCommentResponse:
    return await _community_service(request).add_comment(
        db,
        current_user_id=user.id,
        post_id=post_id,
        payload=payload,
    )


@router.post(
    "/posts/{post_id}/comments/{comment_id}/reactions",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
)
async def react_to_comment(
    post_id: str,
    comment_id: str,
    payload: CommunityReactCommentRequest,
    request: Request,
    user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Response:
    await _community_service(request).react_to_comment(
        db,
        current_user_id=user.id,
        post_id=post_id,
        comment_id=comment_id,
        reaction_code=payload.reactionCode,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/profiles/recent", response_model=list[CommunityProfileResponse])
async def get_recent_profiles(
    request: Request,
    limit: int = Query(default=20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
) -> list[CommunityProfileResponse]:
    return await _community_service(request).list_recent_profiles(db, limit=limit)


@router.get("/profiles/search", response_model=list[CommunityProfileResponse])
async def search_profiles(
    request: Request,
    q: str = Query(default=""),
    limit: int = Query(default=8, ge=1, le=50),
    db: AsyncSession = Depends(get_db),
) -> list[CommunityProfileResponse]:
    return await _community_service(request).search_profiles(db, query=q, limit=limit)


@router.get("/profiles/{uid}", response_model=CommunityProfileResponse)
async def get_profile(
    uid: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> CommunityProfileResponse:
    return await _community_service(request).get_profile(db, uid)


@router.get("/profiles/{uid}/posts", response_model=list[CommunityPostResponse])
async def get_profile_posts(
    uid: str,
    request: Request,
    limit: int = Query(default=30, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
) -> list[CommunityPostResponse]:
    return await _community_service(request).list_posts_by_author(db, author_uid=uid, limit=limit)


@router.get("/profiles/{uid}/comments", response_model=list[CommunityCommentResponse])
async def get_profile_comments(
    uid: str,
    request: Request,
    limit: int = Query(default=40, ge=1, le=120),
    db: AsyncSession = Depends(get_db),
) -> list[CommunityCommentResponse]:
    return await _community_service(request).list_comments_by_author(db, author_uid=uid, limit=limit)


@router.get("/profiles/{uid}/followers/count")
async def get_follower_count(
    uid: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> dict[str, int]:
    return {"count": await _community_service(request).get_follower_count(db, uid=uid)}


@router.get("/profiles/{uid}/following/count")
async def get_following_count(
    uid: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> dict[str, int]:
    return {"count": await _community_service(request).get_following_count(db, uid=uid)}


@router.get("/profiles/{uid}/followers", response_model=list[CommunityProfileResponse])
async def get_followers(
    uid: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> list[CommunityProfileResponse]:
    return await _community_service(request).list_followers(db, uid=uid)


@router.get("/profiles/{uid}/following", response_model=list[CommunityProfileResponse])
async def get_following(
    uid: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> list[CommunityProfileResponse]:
    return await _community_service(request).list_following(db, uid=uid)


@router.get("/profiles/{uid}/follow-state")
async def get_follow_state(
    uid: str,
    request: Request,
    user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, bool]:
    return {"isFollowing": await _community_service(request).is_following(db, viewer_uid=user.id, target_uid=uid)}


@router.get("/profiles/{uid}/holding-summaries", response_model=dict[str, CommunityPublicHoldingSummaryResponse])
async def get_holding_summaries(
    uid: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> dict[str, CommunityPublicHoldingSummaryResponse]:
    return await _community_service(request).get_public_holding_summaries(db, uid=uid)


@router.post("/profiles/sync", response_model=CommunityProfileResponse)
async def sync_profile(
    payload: CommunityProfileUpsertRequest,
    request: Request,
    user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> CommunityProfileResponse:
    return await _community_service(request).sync_profile(db, current_user_id=user.id, payload=payload)


@router.put("/profiles/me", response_model=CommunityProfileResponse)
async def update_profile(
    payload: CommunityProfileUpsertRequest,
    request: Request,
    user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> CommunityProfileResponse:
    return await _community_service(request).update_profile(db, current_user_id=user.id, payload=payload)


@router.post("/profiles/{uid}/follow", status_code=status.HTTP_204_NO_CONTENT, response_class=Response)
async def follow_profile(
    uid: str,
    request: Request,
    user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Response:
    await _community_service(request).follow(db, current_user_id=user.id, target_uid=uid)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete("/profiles/{uid}/follow", status_code=status.HTTP_204_NO_CONTENT, response_class=Response)
async def unfollow_profile(
    uid: str,
    request: Request,
    user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Response:
    await _community_service(request).unfollow(db, current_user_id=user.id, target_uid=uid)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/media/post-image", response_model=CommunityImageUploadResponse)
async def upload_post_image(
    payload: CommunityImageUploadRequest,
    request: Request,
) -> CommunityImageUploadResponse:
    return await _community_service(request).save_media_file(payload, category="community_posts")


@router.post("/media/avatar", response_model=CommunityImageUploadResponse)
async def upload_avatar(
    payload: CommunityImageUploadRequest,
    request: Request,
) -> CommunityImageUploadResponse:
    return await _community_service(request).save_media_file(payload, category="community_profiles/avatars")


@router.post("/media/cover", response_model=CommunityImageUploadResponse)
async def upload_cover(
    payload: CommunityImageUploadRequest,
    request: Request,
) -> CommunityImageUploadResponse:
    return await _community_service(request).save_media_file(payload, category="community_profiles/covers")


@router.get("/chats", response_model=list[ChatConversationOut])
async def get_chats(
    request: Request,
    limit: int = Query(default=30, ge=1, le=100),
    user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[ChatConversationOut]:
    return await list_conversations(
        db=db,
        user_id=user.id,
        limit=limit,
        public_base_url=_public_base_url(request),
    )


@router.get("/chats/unread-count", response_model=UnreadCountOut)
async def get_chat_unread_count(
    user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    cache: RedisCache = Depends(get_cache),
) -> UnreadCountOut:
    return UnreadCountOut(unread_total=await get_unread_total(db=db, cache=cache, user_id=user.id))


@router.get("/presence", response_model=dict[str, bool])
async def get_presence(
    request: Request,
    uids: list[str] = Query(default=[]),
    user: CurrentUser = Depends(get_current_user),
) -> dict[str, bool]:
    del user
    manager = request.app.state.ws_manager
    normalized = {
        uid.strip()
        for uid in uids
        if uid is not None and uid.strip()
    }
    return {uid: manager.is_user_online(uid) for uid in normalized}


@router.get("/chats/{chat_id}/messages", response_model=ChatMessagesPage)
async def get_chat_messages(
    chat_id: str,
    request: Request,
    cursor: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=100),
    user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ChatMessagesPage:
    return await list_messages(
        db=db,
        chat_id=chat_id,
        limit=limit,
        cursor=cursor,
        public_base_url=_public_base_url(request),
    )


@router.get("/chats/with/{peer_id}", response_model=ChatConversationOut | None)
async def get_chat_with_peer(
    peer_id: str,
    request: Request,
    user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ChatConversationOut | None:
    conversation = await get_conversation_by_peer(
        db=db,
        user_id=user.id,
        peer_id=peer_id,
        public_base_url=_public_base_url(request),
    )
    return conversation


@router.get("/chats/with/{peer_id}/messages", response_model=list[ChatMessageOut])
async def get_chat_messages_with_peer(
    peer_id: str,
    request: Request,
    cursor: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=100),
    user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[ChatMessageOut]:
    page = await list_messages_with_peer(
        db=db,
        user_id=user.id,
        peer_id=peer_id,
        limit=limit,
        cursor=cursor,
        public_base_url=_public_base_url(request),
    )
    return list(reversed(page.items))


@router.post("/chats/{chat_id}/messages", response_model=ChatMessageOut, status_code=status.HTTP_201_CREATED)
async def post_chat_message(
    chat_id: str,
    payload: ChatMessageCreate,
    request: Request,
    user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    cache: RedisCache = Depends(get_cache),
    bus: RedisEventBus = Depends(get_bus),
) -> ChatMessageOut:
    return await send_message(
        db=db,
        cache=cache,
        bus=bus,
        chat_id=chat_id,
        sender_id=user.id,
        body=payload.body.strip(),
        message_type=payload.messageType,
        media_url=payload.mediaUrl,
        reply_to_message_id=payload.replyToMessageId,
        notification_service=request.app.state.notification_service,
        public_base_url=_public_base_url(request),
        connection_manager=request.app.state.ws_manager,
    )


@router.post("/chats/with/{peer_id}/messages", response_model=ChatMessageOut, status_code=status.HTTP_201_CREATED)
async def post_chat_message_with_peer(
    peer_id: str,
    payload: ChatMessageCreate,
    request: Request,
    user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    cache: RedisCache = Depends(get_cache),
    bus: RedisEventBus = Depends(get_bus),
) -> ChatMessageOut:
    return await send_message_to_peer(
        db=db,
        cache=cache,
        bus=bus,
        peer_id=peer_id,
        sender_id=user.id,
        body=payload.body.strip(),
        message_type=payload.messageType,
        media_url=payload.mediaUrl,
        reply_to_message_id=payload.replyToMessageId,
        notification_service=request.app.state.notification_service,
        public_base_url=_public_base_url(request),
        connection_manager=request.app.state.ws_manager,
    )


@router.post("/chats/with/{peer_id}/voice", response_model=ChatMessageOut, status_code=status.HTTP_201_CREATED)
async def post_chat_voice_message_with_peer(
    peer_id: str,
    payload: ChatVoiceMessageCreate,
    request: Request,
    user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    cache: RedisCache = Depends(get_cache),
    bus: RedisEventBus = Depends(get_bus),
) -> ChatMessageOut:
    upload = await _community_service(request).save_media_file(
        CommunityImageUploadRequest(
            fileName=payload.fileName,
            contentBase64=payload.contentBase64,
        ),
        category="chat_voice",
    )
    return await send_voice_message_to_peer(
        db=db,
        cache=cache,
        bus=bus,
        peer_id=peer_id,
        sender_id=user.id,
        media_url=upload.path or upload.url,
        duration_ms=payload.durationMs,
        waveform=payload.waveform,
        reply_to_message_id=payload.replyToMessageId,
        notification_service=request.app.state.notification_service,
        public_base_url=_public_base_url(request),
        connection_manager=request.app.state.ws_manager,
    )


@router.put("/chats/messages/{message_id}", response_model=ChatMessageOut)
async def put_chat_message(
    message_id: str,
    payload: ChatMessageUpdate,
    request: Request,
    user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    bus: RedisEventBus = Depends(get_bus),
) -> ChatMessageOut:
    return await update_message_for_user(
        db=db,
        bus=bus,
        message_id=message_id,
        user_id=user.id,
        body=payload.body.strip(),
        public_base_url=_public_base_url(request),
    )


@router.put("/chats/with/{peer_id}/messages/{message_id}", response_model=ChatMessageOut)
async def put_chat_message_with_peer(
    peer_id: str,
    message_id: str,
    payload: ChatMessageUpdate,
    request: Request,
    user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    bus: RedisEventBus = Depends(get_bus),
) -> ChatMessageOut:
    return await update_message_for_user(
        db=db,
        bus=bus,
        message_id=message_id,
        user_id=user.id,
        body=payload.body.strip(),
        peer_id=peer_id,
        public_base_url=_public_base_url(request),
    )


@router.delete("/chats/messages/{message_id}", status_code=status.HTTP_204_NO_CONTENT, response_class=Response)
async def delete_chat_message(
    message_id: str,
    request: Request,
    user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    bus: RedisEventBus = Depends(get_bus),
) -> Response:
    await delete_message_for_user(
        db=db,
        bus=bus,
        message_id=message_id,
        user_id=user.id,
        public_base_url=_public_base_url(request),
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete("/chats/with/{peer_id}/messages/{message_id}", status_code=status.HTTP_204_NO_CONTENT, response_class=Response)
async def delete_chat_message_with_peer(
    peer_id: str,
    message_id: str,
    request: Request,
    user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    bus: RedisEventBus = Depends(get_bus),
) -> Response:
    await delete_message_for_user(
        db=db,
        bus=bus,
        message_id=message_id,
        user_id=user.id,
        peer_id=peer_id,
        public_base_url=_public_base_url(request),
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete("/chats/with/{peer_id}", status_code=status.HTTP_204_NO_CONTENT, response_class=Response)
async def delete_chat_with_peer(
    peer_id: str,
    user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    cache: RedisCache = Depends(get_cache),
    bus: RedisEventBus = Depends(get_bus),
) -> Response:
    await delete_direct_chat_for_user(
        db=db,
        cache=cache,
        bus=bus,
        user_id=user.id,
        peer_id=peer_id,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/chats/with/{peer_id}/delete", status_code=status.HTTP_204_NO_CONTENT, response_class=Response)
async def delete_chat_with_peer_post(
    peer_id: str,
    user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    cache: RedisCache = Depends(get_cache),
    bus: RedisEventBus = Depends(get_bus),
) -> Response:
    await delete_direct_chat_for_user(
        db=db,
        cache=cache,
        bus=bus,
        user_id=user.id,
        peer_id=peer_id,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/chats/{chat_id}/read", status_code=status.HTTP_204_NO_CONTENT, response_class=Response)
async def mark_chat_as_read(
    chat_id: str,
    user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    cache: RedisCache = Depends(get_cache),
    bus: RedisEventBus = Depends(get_bus),
) -> Response:
    await mark_chat_read(db=db, cache=cache, bus=bus, chat_id=chat_id, user_id=user.id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/chats/with/{peer_id}/read", status_code=status.HTTP_204_NO_CONTENT, response_class=Response)
async def mark_chat_as_read_with_peer(
    peer_id: str,
    user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    cache: RedisCache = Depends(get_cache),
    bus: RedisEventBus = Depends(get_bus),
) -> Response:
    await mark_chat_read_with_peer(
        db=db,
        cache=cache,
        bus=bus,
        user_id=user.id,
        peer_id=peer_id,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/chats/read-all", status_code=status.HTTP_204_NO_CONTENT, response_class=Response)
async def mark_all_chats_as_read(
    user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    cache: RedisCache = Depends(get_cache),
    bus: RedisEventBus = Depends(get_bus),
) -> Response:
    await mark_all_chats_read(
        db=db,
        cache=cache,
        bus=bus,
        user_id=user.id,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/posts/{post_id}/reactions", response_model=PostReactionStateOut)
async def post_reaction(
    post_id: str,
    payload: PostReactionChangeIn,
    request: Request,
    user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    cache: RedisCache = Depends(get_cache),
    bus: RedisEventBus = Depends(get_bus),
) -> PostReactionStateOut:
    return await react_to_post(
        db=db,
        cache=cache,
        bus=bus,
        post_id=post_id,
        user_id=user.id,
        reaction_type=payload.reactionKey,
        notification_service=request.app.state.notification_service,
    )

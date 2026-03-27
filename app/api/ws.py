import asyncio
import contextlib

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect
from starlette.datastructures import Headers

from app.db.session import SessionLocal
from app.core.public_url import get_public_base_url_for_websocket
from app.infrastructure.rate_limit.service import RedisRateLimiter
from app.schemas.community import CommunityImageUploadRequest
from app.services.chat_service import (
    delete_direct_chat_for_user,
    delete_message_for_user,
    mark_chat_read_with_peer,
    send_message_to_peer,
    send_voice_message_to_peer,
    update_message_for_user,
)
from app.services.community_service import CommunityService
from app.schemas.ws import WsEnvelope

from app.api.deps import CurrentUser, get_ws_user

router = APIRouter(tags=["ws"])
_ALLOWED_CLIENT_TOPIC_PREFIXES = ("feed:", "presence:")


def _public_base_url(ws: WebSocket) -> str:
    return get_public_base_url_for_websocket(ws)


def _connection_id(ws: WebSocket) -> str:
    state = ws.scope.setdefault("state", {})
    return str(state.get("connection_id") or "")


async def _send_ws_payload(ws: WebSocket, payload: dict) -> None:
    connection_id = _connection_id(ws)
    if not connection_id:
        return
    await ws.app.state.ws_manager.send_to_connection(connection_id, payload)


async def _publish_presence_state(
    ws: WebSocket,
    *,
    user_id: str,
    is_online: bool,
    ) -> None:
    await ws.app.state.bus.publish(
        f"presence:{user_id}",
        WsEnvelope(
            type="chat.presence",
            topic=f"presence:{user_id}",
            data={"user_id": user_id, "is_online": is_online},
        ).model_dump(mode="json"),
    )


@router.websocket("/ws")
async def websocket_endpoint(
    ws: WebSocket,
    user: CurrentUser = Depends(get_ws_user),
):
    manager = ws.app.state.ws_manager
    settings = ws.app.state.settings
    presence_service = ws.app.state.presence_service
    if not await _allow_ws_connect(ws, user.id):
        return
    connection = await manager.connect_user(user.id, ws)
    ws.scope.setdefault("state", {})["connection_id"] = connection.connection_id
    became_online = await presence_service.connect(
        user_id=user.id,
        connection_id=connection.connection_id,
    )
    if became_online:
        await _publish_presence_state(ws, user_id=user.id, is_online=True)

    try:
        while True:
            message = await ws.receive_json()
            if not await _allow_ws_message(ws, user.id):
                return
            action = message.get("action")

            if action == "subscribe_chat":
                chat_id = str(message.get("chat_id", "")).strip()
                if chat_id:
                    join_status = manager.join_room(chat_id, connection.connection_id)
                    if join_status == "limit_reached":
                        await _send_ws_payload(
                            ws,
                            {
                                "type": "error",
                                "topic": "system",
                                "data": {
                                    "message": "Too many active chat subscriptions on this connection.",
                                },
                            },
                        )
                        continue
                    await _send_ws_payload(
                        ws,
                        {"type": "subscribed", "topic": f"room:{chat_id}"},
                    )

            elif action == "unsubscribe_chat":
                chat_id = str(message.get("chat_id", "")).strip()
                if chat_id:
                    manager.leave_room(chat_id, connection.connection_id)

            elif action == "subscribe_topic":
                topic = str(message.get("topic", "")).strip()
                if topic:
                    if not _is_allowed_client_topic(topic):
                        await _send_ws_payload(
                            ws,
                            {
                                "type": "error",
                                "topic": "system",
                                "data": {"message": "Unsupported websocket topic."},
                            },
                        )
                        continue
                    subscribe_status = manager.subscribe_topic(
                        topic,
                        connection.connection_id,
                    )
                    if subscribe_status == "limit_reached":
                        await _send_ws_payload(
                            ws,
                            {
                                "type": "error",
                                "topic": "system",
                                "data": {
                                    "message": "Too many active topic subscriptions on this connection.",
                                },
                            },
                        )
                        continue
                    await _send_ws_payload(ws, {"type": "subscribed", "topic": topic})
                    if subscribe_status == "subscribed":
                        await _send_topic_snapshot(ws, topic)

            elif action == "unsubscribe_topic":
                topic = str(message.get("topic", "")).strip()
                if topic:
                    manager.unsubscribe_topic(topic, connection.connection_id)

            elif action == "ping":
                await _send_ws_payload(
                    ws,
                    {"type": "pong", "heartbeat": settings.ws_heartbeat_seconds},
                )
            elif action == "chat.send":
                await _handle_chat_send(ws, user, message)
            elif action == "chat.send_voice":
                await _handle_chat_send_voice(ws, user, message)
            elif action == "chat.edit":
                await _handle_chat_edit(ws, user, message)
            elif action == "chat.delete":
                await _handle_chat_delete(ws, user, message)
            elif action == "chat.delete_conversation":
                await _handle_chat_delete_conversation(ws, user, message)
            elif action == "chat.read":
                await _handle_chat_read(ws, user, message)
            elif action == "chat.typing":
                await _handle_chat_typing(ws, user, message)
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        await manager.disconnect_connection(connection.connection_id)
        became_offline = await presence_service.disconnect(
            user_id=user.id,
            connection_id=connection.connection_id,
        )
        if became_offline:
            await _publish_presence_state(ws, user_id=user.id, is_online=False)


async def _handle_chat_send(ws: WebSocket, user: CurrentUser, message: dict) -> None:
    request_id = str(message.get("request_id", "")).strip()
    peer_id = str(message.get("peer_id", "")).strip()
    body = str(message.get("body", "")).strip()
    message_type = str(
        message.get("message_type", "") or message.get("messageType", "")
    ).strip()
    media_url = (
        str(message.get("media_url", "") or message.get("mediaUrl", "")).strip()
        or None
    )
    reply_to_message_id = (
        str(
            message.get("reply_to_message_id", "")
            or message.get("replyToMessageId", "")
        )
        .strip()
        or None
    )
    if not peer_id or (not body and media_url is None):
        await _send_command_error(
            ws,
            request_id,
            "peer_id and either body or mediaUrl are required.",
        )
        return

    async with SessionLocal() as db:
        try:
            result = await send_message_to_peer(
                db=db,
                cache=ws.app.state.cache,
                bus=ws.app.state.bus,
                peer_id=peer_id,
                sender_id=user.id,
                body=body,
                message_type=message_type,
                media_url=media_url,
                reply_to_message_id=reply_to_message_id,
                notification_service=ws.app.state.notification_service,
                public_base_url=_public_base_url(ws),
                connection_manager=ws.app.state.ws_manager,
            )
        except Exception as error:
            await _send_command_error(ws, request_id, str(error))
            return

    await _send_ws_payload(
        ws,
        {
            "type": "ack",
            "request_id": request_id,
            "topic": "system",
            "data": {"message": result.model_dump(mode="json")},
        }
    )


async def _handle_chat_send_voice(ws: WebSocket, user: CurrentUser, message: dict) -> None:
    request_id = str(message.get("request_id", "")).strip()
    peer_id = str(message.get("peer_id", "")).strip()
    file_name = str(message.get("fileName", "")).strip()
    content_base64 = str(message.get("contentBase64", "")).strip()
    duration_ms = int(message.get("durationMs", 0) or 0)
    waveform = message.get("waveform", [])
    reply_to_message_id = (
        str(
            message.get("reply_to_message_id", "")
            or message.get("replyToMessageId", "")
        )
        .strip()
        or None
    )
    if not peer_id or not file_name or not content_base64:
        await _send_command_error(
            ws,
            request_id,
            "peer_id, fileName and contentBase64 are required.",
        )
        return

    community_service = CommunityService(
        notification_service=ws.app.state.notification_service,
        bus=ws.app.state.bus,
        public_base_url=_public_base_url(ws),
    )
    async with SessionLocal() as db:
        try:
            upload = await community_service.save_media_file(
                CommunityImageUploadRequest(
                    fileName=file_name,
                    contentBase64=content_base64,
                ),
                category="chat_voice",
            )
            result = await send_voice_message_to_peer(
                db=db,
                cache=ws.app.state.cache,
                bus=ws.app.state.bus,
                peer_id=peer_id,
                sender_id=user.id,
                media_url=upload.path or upload.url,
                duration_ms=max(0, duration_ms),
                waveform=[
                    float(item)
                    for item in list(waveform if isinstance(waveform, list) else [])[:128]
                ],
                reply_to_message_id=reply_to_message_id,
                notification_service=ws.app.state.notification_service,
                public_base_url=_public_base_url(ws),
                connection_manager=ws.app.state.ws_manager,
            )
        except Exception as error:
            await _send_command_error(ws, request_id, str(error))
            return

    await _send_ws_payload(
        ws,
        {
            "type": "ack",
            "request_id": request_id,
            "topic": "system",
            "data": {"message": result.model_dump(mode="json")},
        }
    )


async def _handle_chat_read(ws: WebSocket, user: CurrentUser, message: dict) -> None:
    request_id = str(message.get("request_id", "")).strip()
    peer_id = str(message.get("peer_id", "")).strip()
    if not peer_id:
        await _send_command_error(ws, request_id, "peer_id is required.")
        return

    async with SessionLocal() as db:
        try:
            await mark_chat_read_with_peer(
                db=db,
                cache=ws.app.state.cache,
                bus=ws.app.state.bus,
                user_id=user.id,
                peer_id=peer_id,
            )
        except Exception as error:
            await _send_command_error(ws, request_id, str(error))
            return

    await _send_ws_payload(
        ws,
        {
            "type": "ack",
            "request_id": request_id,
            "topic": "system",
            "data": {"ok": True},
        }
    )


async def _handle_chat_typing(ws: WebSocket, user: CurrentUser, message: dict) -> None:
    peer_id = str(message.get("peer_id", "")).strip()
    if not peer_id:
        return

    is_typing = bool(message.get("is_typing", False) or message.get("isTyping", False))
    await ws.app.state.bus.publish(
        f"user:{peer_id}",
        WsEnvelope(
            type="chat.typing",
            topic=f"user:{peer_id}",
            data={
                "peer_id": user.id,
                "is_typing": is_typing,
            },
        ).model_dump(mode="json"),
    )


async def _handle_chat_edit(ws: WebSocket, user: CurrentUser, message: dict) -> None:
    request_id = str(message.get("request_id", "")).strip()
    message_id = str(message.get("message_id", "")).strip()
    body = str(message.get("body", "")).strip()
    peer_id = str(message.get("peer_id", "")).strip() or None
    if not message_id or not body:
        await _send_command_error(ws, request_id, "message_id and body are required.")
        return

    async with SessionLocal() as db:
        try:
            result = await update_message_for_user(
                db=db,
                bus=ws.app.state.bus,
                message_id=message_id,
                user_id=user.id,
                body=body,
                peer_id=peer_id,
                public_base_url=_public_base_url(ws),
            )
        except Exception as error:
            await _send_command_error(ws, request_id, str(error))
            return

    await _send_ws_payload(
        ws,
        {
            "type": "ack",
            "request_id": request_id,
            "topic": "system",
            "data": {"message": result.model_dump(mode="json")},
        }
    )


async def _handle_chat_delete(ws: WebSocket, user: CurrentUser, message: dict) -> None:
    request_id = str(message.get("request_id", "")).strip()
    message_id = str(message.get("message_id", "")).strip()
    peer_id = str(message.get("peer_id", "")).strip() or None
    if not message_id:
        await _send_command_error(ws, request_id, "message_id is required.")
        return

    async with SessionLocal() as db:
        try:
            await delete_message_for_user(
                db=db,
                bus=ws.app.state.bus,
                message_id=message_id,
                user_id=user.id,
                peer_id=peer_id,
                public_base_url=_public_base_url(ws),
            )
        except Exception as error:
            await _send_command_error(ws, request_id, str(error))
            return

    await _send_ws_payload(
        ws,
        {
            "type": "ack",
            "request_id": request_id,
            "topic": "system",
            "data": {"ok": True},
        }
    )


async def _handle_chat_delete_conversation(
    ws: WebSocket,
    user: CurrentUser,
    message: dict,
) -> None:
    request_id = str(message.get("request_id", "")).strip()
    peer_id = str(message.get("peer_id", "")).strip()
    if not peer_id:
        await _send_command_error(ws, request_id, "peer_id is required.")
        return

    async with SessionLocal() as db:
        try:
            await delete_direct_chat_for_user(
                db=db,
                cache=ws.app.state.cache,
                bus=ws.app.state.bus,
                user_id=user.id,
                peer_id=peer_id,
            )
        except Exception as error:
            await _send_command_error(ws, request_id, str(error))
            return

    await _send_ws_payload(
        ws,
        {
            "type": "ack",
            "request_id": request_id,
            "topic": "system",
            "data": {"ok": True},
        }
    )


async def _allow_ws_connect(ws: WebSocket, user_id: str) -> bool:
    settings = ws.app.state.settings
    if not getattr(settings, "websocket_rate_limit_enabled", False):
        return True
    limiter = getattr(ws.app.state.container, "rate_limiter", None)
    if not isinstance(limiter, RedisRateLimiter):
        return True

    ip = _ws_client_ip(ws)
    ip_decision = await limiter.check(
        key=f"ratelimit:ws:connect:ip:{ip}",
        limit=settings.websocket_rate_limit_max_connects_per_ip,
    )
    if not ip_decision.allowed:
        await ws.close(code=4408, reason="Too many websocket connection attempts.")
        return False
    return True


async def _allow_ws_message(ws: WebSocket, user_id: str) -> bool:
    settings = ws.app.state.settings
    if not getattr(settings, "websocket_rate_limit_enabled", False):
        return True
    limiter = getattr(ws.app.state.container, "rate_limiter", None)
    if not isinstance(limiter, RedisRateLimiter):
        return True

    ip = _ws_client_ip(ws)
    ip_decision = await limiter.check(
        key=f"ratelimit:ws:message:ip:{ip}",
        limit=settings.websocket_rate_limit_max_messages_per_ip,
    )
    if not ip_decision.allowed:
        await _send_ws_payload(
            ws,
            {
                "type": "error",
                "topic": "system",
                "data": {
                    "message": "Too many websocket messages from this IP.",
                    "retryAfter": ip_decision.retry_after_seconds,
                },
            }
        )
        await ws.close(code=4408, reason="Too many websocket messages from this IP.")
        return False

    user_decision = await limiter.check(
        key=f"ratelimit:ws:message:user:{user_id}",
        limit=settings.websocket_rate_limit_max_messages_per_user,
    )
    if not user_decision.allowed:
        await _send_ws_payload(
            ws,
            {
                "type": "error",
                "topic": "system",
                "data": {
                    "message": "Too many websocket messages for this user.",
                    "retryAfter": user_decision.retry_after_seconds,
                },
            }
        )
        await ws.close(code=4408, reason="Too many websocket messages for this user.")
        return False
    return True


async def _send_topic_snapshot(ws: WebSocket, topic: str) -> None:
    if not topic.startswith("presence:"):
        return
    peer_id = topic.split(":", 1)[1].strip()
    if not peer_id:
        return
    is_online = await ws.app.state.presence_service.is_online(peer_id)
    await _send_ws_payload(
        ws,
        WsEnvelope(
            type="chat.presence",
            topic=topic,
            data={
                "user_id": peer_id,
                "is_online": is_online,
            },
        ).model_dump(mode="json")
    )


async def _send_command_error(ws: WebSocket, request_id: str, message: str) -> None:
    await _send_ws_payload(
        ws,
        {
            "type": "error",
            "request_id": request_id,
            "topic": "system",
            "data": {"message": message},
        }
    )


def _ws_client_ip(ws: WebSocket) -> str:
    headers = Headers(scope=ws.scope)
    forwarded_for = headers.get("x-forwarded-for", "").strip()
    if forwarded_for:
        return forwarded_for.split(",", 1)[0].strip()
    client = ws.client
    if client is not None:
        return str(client.host)
    return "unknown"


def _is_allowed_client_topic(topic: str) -> bool:
    normalized = str(topic or "").strip()
    if not normalized or len(normalized) > 160:
        return False
    return any(normalized.startswith(prefix) for prefix in _ALLOWED_CLIENT_TOPIC_PREFIXES)

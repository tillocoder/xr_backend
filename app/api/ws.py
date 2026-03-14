from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect

from app.db.session import SessionLocal
from app.core.public_url import get_public_base_url_for_websocket
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
from app.services.cache import RedisCache
from app.schemas.ws import WsEnvelope
from app.ws.bus import RedisEventBus

from app.api.deps import CurrentUser, get_ws_user

router = APIRouter(tags=["ws"])


def _public_base_url(ws: WebSocket) -> str:
    return get_public_base_url_for_websocket(ws)


@router.websocket("/ws")
async def websocket_endpoint(
    ws: WebSocket,
    user: CurrentUser = Depends(get_ws_user),
):
    manager = ws.app.state.ws_manager
    settings = ws.app.state.settings
    await manager.connect_user(user.id, ws)
    await ws.app.state.bus.publish(
        f"presence:{user.id}",
        WsEnvelope(
            type="chat.presence",
            topic=f"presence:{user.id}",
            data={"user_id": user.id, "is_online": True},
        ).model_dump(mode="json"),
    )

    try:
        while True:
            message = await ws.receive_json()
            action = message.get("action")

            if action == "subscribe_chat":
                chat_id = str(message.get("chat_id", "")).strip()
                if chat_id:
                    manager.join_room(chat_id, ws)
                    await ws.send_json({"type": "subscribed", "topic": f"room:{chat_id}"})

            elif action == "unsubscribe_chat":
                chat_id = str(message.get("chat_id", "")).strip()
                if chat_id:
                    manager.leave_room(chat_id, ws)

            elif action == "subscribe_topic":
                topic = str(message.get("topic", "")).strip()
                if topic:
                    manager.subscribe_topic(topic, ws)
                    await ws.send_json({"type": "subscribed", "topic": topic})

            elif action == "unsubscribe_topic":
                topic = str(message.get("topic", "")).strip()
                if topic:
                    manager.unsubscribe_topic(topic, ws)

            elif action == "ping":
                await ws.send_json({"type": "pong", "heartbeat": settings.ws_heartbeat_seconds})
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
    except WebSocketDisconnect:
        manager.disconnect_user(user.id, ws)
        await ws.app.state.bus.publish(
            f"presence:{user.id}",
            WsEnvelope(
                type="chat.presence",
                topic=f"presence:{user.id}",
                data={
                    "user_id": user.id,
                    "is_online": manager.is_user_online(user.id),
                },
            ).model_dump(mode="json"),
        )


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

    await ws.send_json(
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

    await ws.send_json(
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

    await ws.send_json(
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

    await ws.send_json(
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

    await ws.send_json(
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

    await ws.send_json(
        {
            "type": "ack",
            "request_id": request_id,
            "topic": "system",
            "data": {"ok": True},
        }
    )


async def _send_command_error(ws: WebSocket, request_id: str, message: str) -> None:
    await ws.send_json(
        {
            "type": "error",
            "request_id": request_id,
            "topic": "system",
            "data": {"message": message},
        }
    )

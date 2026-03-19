from __future__ import annotations

import asyncio
import contextlib
from datetime import datetime, timezone

from sqlalchemy import select

from app.db.session import SessionLocal
from app.models.entities import PushToken, User
from app.schemas.ws import WsEnvelope
from app.services.firebase_push_service import FirebasePushService
from app.services.news_feed_service import (
    StoredNewsEntry,
    load_pending_notification_entries,
    mark_news_entries_notified,
    run_news_pipeline,
)
from app.services.push_token_service import PushTokenService
from app.ws.bus import RedisEventBus


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _notification_body_from_summary(summary: str, source: str) -> str:
    for line in str(summary or "").splitlines():
        normalized = line.strip().lstrip("-").strip()
        if normalized:
            return normalized[:180]
    return str(source or "XR HODL").strip() or "XR HODL"


class NewsRuntimeService:
    def __init__(
        self,
        *,
        bus: RedisEventBus,
        firebase_push_service: FirebasePushService,
        push_token_service: PushTokenService,
        poll_interval_seconds: int = 60 * 60,
        max_notifications_per_cycle: int = 5,
    ) -> None:
        self._bus = bus
        self._firebase_push = firebase_push_service
        self._push_token_service = push_token_service
        self._poll_interval_seconds = max(60, int(poll_interval_seconds))
        self._max_notifications_per_cycle = max(1, int(max_notifications_per_cycle))
        self._task: asyncio.Task | None = None
        self._started = False

    async def start(self) -> None:
        if self._started:
            return
        self._started = True
        self._task = asyncio.create_task(self._run_loop())

    async def stop(self) -> None:
        task = self._task
        self._task = None
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    async def _run_loop(self) -> None:
        try:
            while True:
                await asyncio.sleep(self._poll_interval_seconds)
                await self._refresh_once()
        except asyncio.CancelledError:
            raise

    async def _refresh_once(self) -> None:
        async with SessionLocal() as db:
            try:
                await run_news_pipeline(db)
                pending_entries = await load_pending_notification_entries(
                    db,
                    limit=self._max_notifications_per_cycle,
                )
            except Exception:
                await db.rollback()
                return

            sent_article_ids: list[int] = []
            for entry in pending_entries:
                try:
                    await self._publish_update(entry)
                    await self._push_update(entry)
                except Exception:
                    continue
                sent_article_ids.append(entry.article_id)

            if sent_article_ids:
                await mark_news_entries_notified(
                    db,
                    article_ids=sent_article_ids,
                    now=_utc_now(),
                )

    async def _publish_update(self, entry: StoredNewsEntry) -> None:
        payload = entry.to_event_payload()
        await self._bus.publish(
            "feed:news",
            WsEnvelope(
                type="news.update",
                topic="feed:news",
                data={
                    "item": payload,
                    "updatedAt": _utc_now().isoformat(),
                },
            ).model_dump(mode="json"),
        )

    async def _push_update(self, entry: StoredNewsEntry) -> None:
        if not self._firebase_push.is_configured:
            return

        async with SessionLocal() as db:
            rows = (
                await db.execute(
                    select(PushToken.token, User.settings_json)
                    .join(User, User.id == PushToken.user_id)
                    .order_by(PushToken.updated_at.desc())
                )
            ).all()

        token_groups: dict[str, list[str]] = {"en": [], "uz": [], "ru": []}
        for token, settings_json in rows:
            normalized_token = str(token or "").strip()
            if not normalized_token:
                continue
            lang = self._language_from_settings(settings_json)
            token_groups.setdefault(lang, []).append(normalized_token)

        if not any(token_groups.values()):
            return

        invalid_tokens: list[str] = []
        for lang, tokens in token_groups.items():
            if not tokens:
                continue
            data = entry.to_payload_item(lang=lang)
            data["kind"] = "news"
            payload = {key: str(value) for key, value in data.items() if str(value).strip()}
            title = payload.get("title", "") or entry.source
            body = _notification_body_from_summary(payload.get("summary", ""), entry.source)
            for start in range(0, len(tokens), 500):
                chunk = tokens[start : start + 500]
                invalid_tokens.extend(
                    self._firebase_push.send_to_tokens(
                        tokens=chunk,
                        title=title,
                        body=body,
                        data=payload,
                    )
                )

        if not invalid_tokens:
            return

        async with SessionLocal() as db:
            await self._push_token_service.remove_tokens(db, invalid_tokens)

    def _language_from_settings(self, settings_json: object) -> str:
        if isinstance(settings_json, dict):
            raw = str(settings_json.get("language") or "").strip().lower()
            if raw in {"en", "uz", "ru"}:
                return raw
        return "uz"

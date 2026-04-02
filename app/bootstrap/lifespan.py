from __future__ import annotations

from contextlib import asynccontextmanager
import logging

from sqlalchemy import text

from app.application.system.services import SystemStatusService
from app.bootstrap.container import AppContainer
from app.bootstrap.runtime_schema import ensure_runtime_tables
from app.core.config import get_settings
from app.db.base import Base
from app.db.session import SessionLocal, engine
from app.infrastructure.health.probes import DatabaseHealthProbe, RedisHealthProbe
from app.infrastructure.rate_limit.service import RedisRateLimiter
from app.services.ai_provider_config_service import (
    ensure_gemini_config_row,
    rebalance_gemini_config_rows,
)
from app.services.auth_session_service import AuthSessionService
from app.services.cache import RedisCache
from app.services.firebase_push_service import FirebasePushService
from app.services.google_identity_service import GoogleIdentityService
from app.services.market_runtime_service import MarketRuntimeService
from app.services.news_runtime_service import NewsRuntimeService
from app.services.notification_service import NotificationService
from app.services.news_feed_service import ensure_articles_ingested
from app.services.news_query_service import build_news_feed_payload
from app.services.presence_service import PresenceService
from app.services.presence_runtime_service import PresenceRuntimeService
from app.services.push_token_service import PushTokenService
from app.services.runtime_lease_service import RuntimeLeaseService
from app.ws.bus import RedisEventBus
from app.ws.manager import ConnectionManager

_STARTUP_INIT_LOCK_KEY = 60241603
_STARTUP_NEWS_WARM_LIMIT = 3


def build_container() -> AppContainer:
    settings = get_settings()
    cache = RedisCache(settings.redis_url)
    bus = RedisEventBus(
        settings.redis_url,
        allow_local_fallback=not settings.redis_required_for_runtime,
    )
    runtime_lease_service = RuntimeLeaseService(
        cache,
        allow_best_effort_fallback=not settings.redis_required_for_runtime,
    )
    ws_manager = ConnectionManager(
        send_timeout_seconds=settings.websocket_send_timeout_seconds,
        max_pending_messages=settings.websocket_max_pending_messages_per_connection,
        max_rooms_per_connection=settings.websocket_max_rooms_per_connection,
        max_topics_per_connection=settings.websocket_max_topics_per_connection,
    )
    presence_service = PresenceService(
        cache,
        ttl_seconds=settings.websocket_presence_ttl_seconds,
        refresh_interval_seconds=settings.websocket_presence_refresh_interval_seconds,
    )
    presence_runtime_service = PresenceRuntimeService(
        manager=ws_manager,
        presence_service=presence_service,
    )
    push_token_service = PushTokenService()
    auth_session_service = AuthSessionService()
    firebase_push_service = FirebasePushService(settings)
    google_identity_service = GoogleIdentityService(settings)
    news_runtime_service = NewsRuntimeService(
        bus=bus,
        firebase_push_service=firebase_push_service,
        push_token_service=push_token_service,
        lease_service=runtime_lease_service,
        max_notifications_per_cycle=1,
    )
    notification_service = NotificationService(
        push_token_service=push_token_service,
        firebase_push_service=firebase_push_service,
        bus=bus,
    )
    market_runtime_service = MarketRuntimeService(
        settings=settings,
        cache=cache,
        notification_service=notification_service,
        lease_service=runtime_lease_service,
    )
    system_status_service = SystemStatusService(
        probes=(
            DatabaseHealthProbe(engine),
            RedisHealthProbe(cache),
        )
    )
    rate_limiter = None
    if settings.rate_limit_enabled:
        rate_limiter = RedisRateLimiter(
            cache,
            window_seconds=settings.rate_limit_window_seconds,
        )
    return AppContainer(
        settings=settings,
        cache=cache,
        bus=bus,
        ws_manager=ws_manager,
        push_token_service=push_token_service,
        auth_session_service=auth_session_service,
        firebase_push_service=firebase_push_service,
        google_identity_service=google_identity_service,
        news_runtime_service=news_runtime_service,
        market_runtime_service=market_runtime_service,
        notification_service=notification_service,
        presence_service=presence_service,
        presence_runtime_service=presence_runtime_service,
        runtime_lease_service=runtime_lease_service,
        system_status_service=system_status_service,
        rate_limiter=rate_limiter,
    )


async def _run_startup_initialization(container: AppContainer) -> None:
    if container.settings.redis_required_for_runtime and not await container.cache.ping():
        raise RuntimeError("Redis is required for runtime coordination but is unavailable.")
    async with SessionLocal() as db:
        await db.execute(
            text("SELECT pg_advisory_xact_lock(:lock_key)"),
            {"lock_key": _STARTUP_INIT_LOCK_KEY},
        )
        connection = await db.connection()
        try:
            if container.settings.auto_create_schema:
                await connection.run_sync(Base.metadata.create_all)
            await ensure_runtime_tables(connection)
            await ensure_gemini_config_row(db)
            await rebalance_gemini_config_rows(db)
            await db.commit()
        except Exception:
            await db.rollback()
            raise
        try:
            english_feed = await build_news_feed_payload(
                db,
                lang="en",
                limit=_STARTUP_NEWS_WARM_LIMIT,
            )
            if not english_feed.get("latest") and not english_feed.get("liquidations"):
                inserted = await ensure_articles_ingested(
                    db,
                    max_each_feed=_STARTUP_NEWS_WARM_LIMIT,
                    enable_ai_dedup=False,
                )
                if inserted > 0:
                    logging.getLogger(__name__).info(
                        "startup_news_catalog_warmed",
                        extra={"inserted": inserted},
                    )
        except Exception:
            logging.getLogger(__name__).warning(
                "startup_news_catalog_warm_failed",
                exc_info=True,
            )


def _should_start_coordinated_runtime_services(container: AppContainer) -> bool:
    settings = container.settings
    if settings.coordinated_runtime_services_enabled:
        return True
    app_logger = logging.getLogger(__name__)
    app_logger.warning(
        "coordinated_runtime_services_disabled",
        extra={
            "reason": "redis_required_for_runtime=false with multiple workers",
            "workers": settings.process_worker_count,
        },
    )
    return False


@asynccontextmanager
async def lifespan(app):
    container = build_container()
    container.attach_to_app(app)
    await _run_startup_initialization(container)

    await container.bus.start(container.ws_manager.dispatch)
    await container.presence_runtime_service.start()
    coordinated_runtime_services_started = _should_start_coordinated_runtime_services(container)
    if coordinated_runtime_services_started:
        await container.news_runtime_service.start()
        await container.market_runtime_service.start()
    try:
        yield
    finally:
        if coordinated_runtime_services_started:
            await container.market_runtime_service.stop()
            await container.news_runtime_service.stop()
        await container.presence_runtime_service.stop()
        await container.bus.stop()
        await container.cache.close()

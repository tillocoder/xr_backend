from __future__ import annotations

from contextlib import asynccontextmanager

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
from app.services.market_runtime_service import MarketRuntimeService
from app.services.news_runtime_service import NewsRuntimeService
from app.services.notification_service import NotificationService
from app.services.push_token_service import PushTokenService
from app.ws.bus import RedisEventBus
from app.ws.manager import ConnectionManager


def build_container() -> AppContainer:
    settings = get_settings()
    cache = RedisCache(settings.redis_url)
    bus = RedisEventBus(settings.redis_url)
    ws_manager = ConnectionManager()
    push_token_service = PushTokenService()
    auth_session_service = AuthSessionService()
    firebase_push_service = FirebasePushService(settings)
    news_runtime_service = NewsRuntimeService(
        bus=bus,
        firebase_push_service=firebase_push_service,
        push_token_service=push_token_service,
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
        news_runtime_service=news_runtime_service,
        market_runtime_service=market_runtime_service,
        notification_service=notification_service,
        system_status_service=system_status_service,
        rate_limiter=rate_limiter,
    )


@asynccontextmanager
async def lifespan(app):
    container = build_container()
    container.attach_to_app(app)

    async with engine.begin() as connection:
        if container.settings.auto_create_schema:
            await connection.run_sync(Base.metadata.create_all)
        await ensure_runtime_tables(connection)

    async with SessionLocal() as db:
        try:
            await ensure_gemini_config_row(db)
            await rebalance_gemini_config_rows(db)
            await db.commit()
        except Exception:
            await db.rollback()

    await container.bus.start(container.ws_manager.dispatch)
    await container.news_runtime_service.start()
    await container.market_runtime_service.start()
    try:
        yield
    finally:
        await container.market_runtime_service.stop()
        await container.news_runtime_service.stop()
        await container.bus.stop()
        await container.cache.close()

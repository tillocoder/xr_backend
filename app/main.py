from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

from app.api.compat import router as compat_router
from app.api.community import router as community_router
from app.api.learning import admin_router as learning_admin_router
from app.api.learning import router as learning_router
from app.api.me import router as me_router
from app.api.ws import router as ws_router
from app.admin_panel import setup_admin_panel
from app.core.config import get_settings
from app.db.base import Base
from app.db.session import engine
from app.services.cache import RedisCache
from app.services.auth_session_service import AuthSessionService
from app.services.firebase_push_service import FirebasePushService
from app.services.notification_service import NotificationService
from app.services.push_token_service import PushTokenService
from app.ws.bus import RedisEventBus
from app.ws.manager import ConnectionManager


MEDIA_ROOT = Path(__file__).resolve().parents[1] / "media"


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    cache = RedisCache(settings.redis_url)
    bus = RedisEventBus(settings.redis_url)
    ws_manager = ConnectionManager()
    push_token_service = PushTokenService()
    auth_session_service = AuthSessionService()
    firebase_push_service = FirebasePushService(settings)
    notification_service = NotificationService(
        push_token_service=push_token_service,
        firebase_push_service=firebase_push_service,
        bus=bus,
    )

    app.state.settings = settings
    app.state.cache = cache
    app.state.bus = bus
    app.state.ws_manager = ws_manager
    app.state.push_token_service = push_token_service
    app.state.auth_session_service = auth_session_service
    app.state.firebase_push_service = firebase_push_service
    app.state.notification_service = notification_service
    app.state.user_settings = {}
    app.state.user_wallets = {}

    async with engine.begin() as connection:
        if settings.auto_create_schema:
            await connection.run_sync(Base.metadata.create_all)
        await _ensure_runtime_tables(connection)

    await bus.start(ws_manager.dispatch)
    try:
        yield
    finally:
        await bus.stop()
        await cache.close()


settings = get_settings()
app = FastAPI(title=settings.project_name, lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

setup_admin_panel(app)

app.include_router(compat_router, prefix=settings.api_prefix)
app.include_router(community_router, prefix=settings.api_prefix)
app.include_router(learning_router, prefix=settings.api_prefix)
app.include_router(me_router, prefix=settings.api_prefix)
app.include_router(ws_router, prefix=settings.api_prefix)
app.include_router(learning_admin_router)
app.mount("/media", StaticFiles(directory=str(MEDIA_ROOT), check_dir=False), name="media")


@app.get("/health")
async def health() -> dict[str, bool]:
    return {"ok": True}


async def _ensure_runtime_tables(connection) -> None:
    await connection.execute(
        text(
            """
            ALTER TABLE users
            ADD COLUMN IF NOT EXISTS diamonds_balance INTEGER NOT NULL DEFAULT 0
            """
        )
    )
    await connection.execute(
        text(
            """
            ALTER TABLE users
            ADD COLUMN IF NOT EXISTS daily_reward_streak INTEGER NOT NULL DEFAULT 0
            """
        )
    )
    await connection.execute(
        text(
            """
            ALTER TABLE users
            ADD COLUMN IF NOT EXISTS daily_reward_last_claimed_at TIMESTAMPTZ NULL
            """
        )
    )
    await connection.execute(
        text(
            """
            ALTER TABLE users
            ADD COLUMN IF NOT EXISTS reward_pro_expires_at TIMESTAMPTZ NULL
            """
        )
    )
    await connection.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS auth_sessions (
                id VARCHAR(32) PRIMARY KEY,
                user_id VARCHAR(32) NOT NULL REFERENCES users(id),
                access_token_hash VARCHAR(64) NOT NULL UNIQUE,
                refresh_token_hash VARCHAR(64) NOT NULL UNIQUE,
                access_expires_at TIMESTAMPTZ NOT NULL,
                refresh_expires_at TIMESTAMPTZ NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                last_seen_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
    )
    await connection.execute(
        text("CREATE INDEX IF NOT EXISTS ix_auth_sessions_user_id ON auth_sessions (user_id)")
    )
    await connection.execute(
        text(
            "CREATE INDEX IF NOT EXISTS ix_auth_sessions_access_token_hash ON auth_sessions (access_token_hash)"
        )
    )
    await connection.execute(
        text(
            "CREATE INDEX IF NOT EXISTS ix_auth_sessions_refresh_token_hash ON auth_sessions (refresh_token_hash)"
        )
    )

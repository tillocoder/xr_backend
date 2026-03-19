from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.staticfiles import StaticFiles

from app.admin_panel import setup_admin_panel
from app.api.admin import router as admin_router
from app.api.compat import router as compat_router
from app.api.community import router as community_router
from app.api.learning import admin_router as learning_admin_router
from app.api.learning import router as learning_router
from app.api.me import router as me_router
from app.api.ws import router as ws_router
from app.bootstrap.lifespan import lifespan
from app.core.config import get_settings
from app.core.logging import configure_logging
from app.presentation.api.system import router as system_router
from app.presentation.http.middleware import ObservabilityMiddleware, RateLimitMiddleware


MEDIA_ROOT = Path(__file__).resolve().parents[2] / "media"


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.request_log_level)

    app = FastAPI(title=settings.project_name, lifespan=lifespan)
    app.add_middleware(GZipMiddleware, minimum_size=max(256, settings.gzip_minimum_size_bytes))
    app.add_middleware(
        RateLimitMiddleware,
        settings=settings,
        limiter=None,
    )
    app.add_middleware(ObservabilityMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    setup_admin_panel(app)

    app.include_router(system_router)
    app.include_router(compat_router, prefix=settings.api_prefix)
    app.include_router(admin_router, prefix=settings.api_prefix)
    app.include_router(community_router, prefix=settings.api_prefix)
    app.include_router(learning_router, prefix=settings.api_prefix)
    app.include_router(me_router, prefix=settings.api_prefix)
    app.include_router(ws_router, prefix=settings.api_prefix)
    app.include_router(learning_admin_router)
    app.mount("/media", StaticFiles(directory=str(MEDIA_ROOT), check_dir=False), name="media")
    return app

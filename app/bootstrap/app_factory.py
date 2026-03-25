from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.staticfiles import StaticFiles

from app.admin_panel import setup_admin_panel
from app.api.admin import router as admin_router
from app.api.compat import router as compat_router
from app.api.community import router as community_router
from app.api.learning import admin_router as learning_admin_router
from app.api.learning import router as learning_router
from app.api.me import router as me_router
from app.api.signals import router as signals_router
from app.api.ws import router as ws_router
from app.bootstrap.lifespan import lifespan
from app.core.config import get_settings
from app.core.logging import configure_logging
from app.presentation.api.system import router as system_router
from app.presentation.http.middleware import (
    ObservabilityMiddleware,
    RateLimitMiddleware,
    SecurityHeadersMiddleware,
)


MEDIA_ROOT = Path(__file__).resolve().parents[2] / "media"
LOGGER = logging.getLogger("app.bootstrap")


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
    app.add_middleware(SecurityHeadersMiddleware, settings=settings)
    app.add_middleware(
        TrustedHostMiddleware,
        allowed_hosts=settings.trusted_hosts_list,
    )
    app.add_middleware(ObservabilityMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_allowed_origins_list,
        allow_origin_regex=settings.cors_allow_origin_regex or None,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=[
            "Authorization",
            "Content-Type",
            "X-Requested-With",
            "X-Request-ID",
        ],
    )

    app.include_router(system_router)
    app.include_router(compat_router, prefix=settings.api_prefix)
    app.include_router(community_router, prefix=settings.api_prefix)
    app.include_router(learning_router, prefix=settings.api_prefix)
    app.include_router(me_router, prefix=settings.api_prefix)
    app.include_router(signals_router, prefix=settings.api_prefix)
    app.include_router(ws_router, prefix=settings.api_prefix)
    if settings.admin_features_enabled:
        setup_admin_panel(app)
        app.include_router(admin_router, prefix=settings.api_prefix)
        app.include_router(learning_admin_router)
    elif settings.admin_panel_enabled:
        LOGGER.warning("admin_features_disabled_insecure_credentials")
    app.mount("/media", StaticFiles(directory=str(MEDIA_ROOT), check_dir=False), name="media")
    return app

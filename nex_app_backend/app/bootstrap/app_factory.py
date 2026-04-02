from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.staticfiles import StaticFiles

from app.admin_panel import setup_admin_panel
from app.bootstrap.lifespan import lifespan
from app.core.config import get_settings
from app.core.logging import configure_logging
from app.modules.system.presentation import router as system_router
from app.presentation.api.v1 import router as api_v1_router
from app.presentation.http.exceptions import register_exception_handlers
from app.presentation.http.middleware import (
    ObservabilityMiddleware,
    SecurityHeadersMiddleware,
)


MEDIA_ROOT = Path(__file__).resolve().parents[2] / "media"


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.request_log_level, json_logs=settings.json_logs)

    app = FastAPI(
        title=settings.project_name,
        debug=bool(settings.debug),
        lifespan=lifespan,
        docs_url="/docs" if settings.api_docs_enabled else None,
        redoc_url="/redoc" if settings.api_docs_enabled else None,
        openapi_url="/openapi.json" if settings.api_docs_enabled else None,
    )
    app.state.settings = settings
    register_exception_handlers(app)
    app.add_middleware(GZipMiddleware, minimum_size=max(256, settings.gzip_minimum_size_bytes))
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(
        TrustedHostMiddleware,
        allowed_hosts=settings.trusted_hosts_list,
    )
    app.add_middleware(ObservabilityMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_allowed_origins_list,
        allow_origin_regex=settings.cors_allow_origin_regex,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-Requested-With", "X-Request-ID"],
    )

    app.include_router(system_router)
    app.include_router(api_v1_router, prefix=settings.api_prefix)
    if settings.admin_panel_enabled:
        setup_admin_panel(app)
    app.mount("/media", StaticFiles(directory=str(MEDIA_ROOT), check_dir=False), name="media")
    return app

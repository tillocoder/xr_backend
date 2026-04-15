from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.staticfiles import StaticFiles

from app.admin_panel import setup_admin_panel
from app.bootstrap.lifespan import lifespan
from app.core.config import get_settings
from app.core.logging import configure_logging
from app.presentation.api.admin import learning_admin_router
from app.presentation.api.admin import router as admin_router
from app.presentation.api.v1 import router as api_v1_router
from app.modules.system.presentation import router as system_router
from app.presentation.http.admin_home import build_admin_home_html
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

    app = FastAPI(
        title=settings.project_name,
        lifespan=lifespan,
        openapi_url=settings.openapi_url_path,
        docs_url=settings.docs_url_path,
        redoc_url=settings.redoc_url_path,
    )
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
    app.include_router(api_v1_router, prefix=settings.api_prefix)
    if settings.admin_features_enabled:
        setup_admin_panel(app)

        async def admin_home(request: Request) -> HTMLResponse:
            current_origin = str(request.base_url).rstrip("/")
            html = build_admin_home_html(
                current_origin=current_origin,
                public_origin=settings.public_origin or current_origin,
                project_name=settings.project_name,
                api_prefix=settings.api_prefix,
                show_api_docs=settings.api_docs_enabled,
            )
            return HTMLResponse(html)

        app.add_api_route(
            "/admin",
            admin_home,
            include_in_schema=False,
        )
        app.add_api_route(
            "/admin/",
            admin_home,
            include_in_schema=False,
        )
        app.include_router(admin_router, prefix=settings.api_prefix)
        app.include_router(learning_admin_router)
    app.mount("/media", StaticFiles(directory=str(MEDIA_ROOT), check_dir=False), name="media")
    return app

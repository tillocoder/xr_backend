from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.bootstrap.container import AppContainer
from app.bootstrap.runtime_schema import ensure_runtime_schema
from app.core.config import get_settings
from app.db.session import engine


LOGGER = logging.getLogger("app.bootstrap")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    app.state.container = AppContainer(settings=settings)
    await ensure_runtime_schema(settings.auto_create_schema)
    LOGGER.info("nex_backend_startup_complete")
    try:
        yield
    finally:
        await engine.dispose()
        LOGGER.info("nex_backend_shutdown_complete")

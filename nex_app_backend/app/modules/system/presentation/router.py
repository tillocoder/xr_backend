from __future__ import annotations

from fastapi import APIRouter

from app.core.config import get_settings
from app.schemas.system import HealthResponse


router = APIRouter(tags=["system"])


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    return HealthResponse(status="ok", service="nex_app_backend")


@router.get("/ready", response_model=HealthResponse)
async def ready() -> HealthResponse:
    return HealthResponse(status="ready", service="nex_app_backend")


@router.get("/", response_model=HealthResponse, include_in_schema=False)
async def root() -> HealthResponse:
    return HealthResponse(status="ok", service=get_settings().project_name)


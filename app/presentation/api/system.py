from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response
from starlette.responses import JSONResponse
from starlette.status import HTTP_503_SERVICE_UNAVAILABLE

from app.api.deps import get_settings, get_system_status_service
from app.application.system.services import SystemStatusService
from app.presentation.http.middleware import render_metrics


router = APIRouter(tags=["system"])


@router.get("/health")
async def health(
    service: SystemStatusService = Depends(get_system_status_service),
) -> dict[str, bool]:
    return service.health_payload()


@router.get("/ready")
async def ready(
    service: SystemStatusService = Depends(get_system_status_service),
) -> dict[str, object]:
    report = await service.readiness_report()
    payload = report.to_payload()
    if not report.ok:
        return JSONResponse(status_code=HTTP_503_SERVICE_UNAVAILABLE, content=payload)
    return payload


@router.get("/metrics")
async def metrics(
    settings=Depends(get_settings),
) -> Response:
    if not settings.metrics_enabled:
        raise HTTPException(status_code=404, detail="Metrics are disabled.")
    payload, media_type = render_metrics()
    return Response(content=payload, media_type=media_type)

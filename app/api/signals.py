from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, get_current_user
from app.db.session import get_db
from app.presentation.api.request_state import get_market_runtime_service


router = APIRouter(prefix="/me/signals", tags=["signals"])


class SignalPreferencesPayload(BaseModel):
    enabled: bool = True
    priceTargetEnabled: bool = True
    percentChangeEnabled: bool = True
    volumeSpikeEnabled: bool = True
    fearGreedExtremeEnabled: bool = True
    whaleActivityEnabled: bool = True
    percentThreshold: float = 5.0
    volumeMultiplier: float = 1.8


class TargetAlertCreatePayload(BaseModel):
    symbol: str = Field(min_length=1, max_length=24)
    targetPrice: float = Field(gt=0)


def _service_from_request(request: Request):
    return get_market_runtime_service(request)


def _raise_signal_service_error(error: Exception) -> None:
    if isinstance(error, PermissionError):
        raise HTTPException(status_code=403, detail=str(error)) from error
    if isinstance(error, ValueError):
        raise HTTPException(status_code=400, detail=str(error)) from error
    raise error


@router.get("/bootstrap")
async def get_signal_bootstrap(
    request: Request,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    service = _service_from_request(request)
    return await service.get_signal_bootstrap(db, user_id=current_user.id)


@router.put("/preferences")
async def update_signal_preferences(
    payload: SignalPreferencesPayload,
    request: Request,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    service = _service_from_request(request)
    try:
        preferences = await service.update_preferences(
            db,
            user_id=current_user.id,
            payload=payload.model_dump(mode="json"),
        )
    except (PermissionError, ValueError) as error:
        _raise_signal_service_error(error)
    return {"ok": True, "preferences": preferences}


@router.get("/targets")
async def list_signal_targets(
    request: Request,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    service = _service_from_request(request)
    return {"items": await service.list_target_alerts(db, user_id=current_user.id)}


@router.post("/targets")
async def create_signal_target(
    payload: TargetAlertCreatePayload,
    request: Request,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    service = _service_from_request(request)
    try:
        item = await service.create_target_alert(
            db,
            user_id=current_user.id,
            symbol=payload.symbol,
            target_price=payload.targetPrice,
        )
    except (PermissionError, ValueError) as error:
        _raise_signal_service_error(error)
    return {"item": item}


@router.delete("/targets/{target_id}", status_code=status.HTTP_204_NO_CONTENT, response_class=Response)
async def delete_signal_target(
    target_id: str,
    request: Request,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Response:
    service = _service_from_request(request)
    deleted = await service.delete_target_alert(db, user_id=current_user.id, target_id=target_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Target alert was not found.")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/refresh")
async def refresh_signal_market_snapshot(
    request: Request,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    del current_user
    service = _service_from_request(request)
    return await service.refresh_market_snapshot(db, force_refresh=True)

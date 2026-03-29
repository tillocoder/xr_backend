from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, get_current_user
from app.db.session import get_db
from app.modules.account.application import MeService
from app.presentation.api.request_state import (
    get_notification_service,
    get_push_token_service,
)
from app.schemas.me import (
    DailyRewardStatusResponse,
    MeBootstrapPayload,
    MeHoldingsPayload,
    MembershipCatalogResponse,
    MeWalletsPayload,
    MeWatchlistPayload,
)
from app.schemas.notification import (
    MarkNotificationsReadRequest,
    NotificationListResponse,
    PushTokenPayload,
)


router = APIRouter(prefix="/me", tags=["me"])
_ME_SERVICE = MeService()


@router.get("/bootstrap")
async def get_bootstrap(
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    return await _ME_SERVICE.get_bootstrap(db, user_id=current_user.id)


@router.get("/daily-reward", response_model=DailyRewardStatusResponse)
async def get_daily_reward_status(
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> DailyRewardStatusResponse:
    return await _ME_SERVICE.get_daily_reward_status(db, user_id=current_user.id)


@router.post("/daily-reward/claim", response_model=DailyRewardStatusResponse)
async def claim_daily_reward(
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> DailyRewardStatusResponse:
    return await _ME_SERVICE.claim_daily_reward(db, user_id=current_user.id)


@router.get("/membership/offers", response_model=MembershipCatalogResponse)
async def get_membership_offers(
    current_user: CurrentUser = Depends(get_current_user),
) -> MembershipCatalogResponse:
    del current_user
    return _ME_SERVICE.get_membership_catalog()


@router.put("/settings")
async def update_settings(
    payload: dict[str, Any],
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    return await _ME_SERVICE.update_settings(db, user_id=current_user.id, payload=payload)


@router.put("/holdings")
async def update_holdings(
    payload: MeHoldingsPayload,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    return await _ME_SERVICE.update_holdings(
        db,
        user_id=current_user.id,
        items=payload.items,
    )


@router.put("/wallets")
async def update_wallets(
    payload: MeWalletsPayload,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    return await _ME_SERVICE.update_wallets(
        db,
        user_id=current_user.id,
        items=payload.items,
    )


@router.put("/watchlist")
async def update_watchlist(
    payload: MeWatchlistPayload,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    return await _ME_SERVICE.update_watchlist(
        db,
        user_id=current_user.id,
        symbols=payload.symbols,
    )


@router.put("/bootstrap")
async def update_bootstrap(
    payload: MeBootstrapPayload,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    return await _ME_SERVICE.update_bootstrap(
        db,
        user_id=current_user.id,
        payload=payload,
    )


@router.get("/notifications", response_model=NotificationListResponse)
async def get_notifications(
    request: Request,
    limit: int = 20,
    unread_only: bool = True,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> NotificationListResponse:
    service = get_notification_service(request)
    return await service.list_notifications(
        db,
        user_id=current_user.id,
        limit=limit,
        unread_only=unread_only,
    )


@router.post("/notifications/read", status_code=status.HTTP_204_NO_CONTENT, response_class=Response)
async def mark_notifications_read(
    payload: MarkNotificationsReadRequest,
    request: Request,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Response:
    service = get_notification_service(request)
    await service.mark_read(db, user_id=current_user.id, ids=payload.ids)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/notifications/read-all", status_code=status.HTTP_204_NO_CONTENT, response_class=Response)
async def mark_all_notifications_read(
    request: Request,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Response:
    service = get_notification_service(request)
    await service.mark_all_read(db, user_id=current_user.id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/push-token", status_code=status.HTTP_204_NO_CONTENT, response_class=Response)
async def register_push_token(
    payload: PushTokenPayload,
    request: Request,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Response:
    service = get_push_token_service(request)
    await service.register_token(
        db,
        user_id=current_user.id,
        token=payload.token,
        platform=payload.platform,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete("/push-token", status_code=status.HTTP_204_NO_CONTENT, response_class=Response)
async def unregister_push_token(
    payload: PushTokenPayload,
    request: Request,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Response:
    service = get_push_token_service(request)
    await service.unregister_token(
        db,
        user_id=current_user.id,
        token=payload.token,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)

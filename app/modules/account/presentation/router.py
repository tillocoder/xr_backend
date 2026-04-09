from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, Response, UploadFile, status
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
    PortfolioVoiceCommandRequest,
    PortfolioVoiceCommandResponse,
    MembershipCatalogResponse,
    MeWalletsPayload,
    MeWatchlistPayload,
)
from app.schemas.notification import (
    MarkNotificationsReadRequest,
    NotificationListResponse,
    PushTokenPayload,
)
from app.services.portfolio_voice_command_service import (
    PortfolioVoiceInterpretationError,
    PortfolioVoiceNotConfiguredError,
    PortfolioVoiceUnavailableError,
)


router = APIRouter(prefix="/me", tags=["me"])
_ME_SERVICE = MeService()
LOGGER = logging.getLogger(__name__)


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


@router.post("/portfolio/voice-command", response_model=PortfolioVoiceCommandResponse)
async def process_portfolio_voice_command(
    payload: PortfolioVoiceCommandRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> PortfolioVoiceCommandResponse:
    try:
        return await _ME_SERVICE.process_portfolio_voice_command(
            db,
            user_id=current_user.id,
            transcript=payload.transcript,
            app_language_code=payload.appLanguageCode,
            speech_locale_id=payload.speechLocaleId,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except PortfolioVoiceNotConfiguredError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except PortfolioVoiceUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except PortfolioVoiceInterpretationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/portfolio/voice-command/audio", response_model=PortfolioVoiceCommandResponse)
async def process_portfolio_voice_audio(
    app_language_code: str = Form("en"),
    speech_locale_id: str | None = Form(None),
    audio: UploadFile = File(...),
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> PortfolioVoiceCommandResponse:
    try:
        audio_bytes = await audio.read()
        return await _ME_SERVICE.process_portfolio_voice_audio(
            db,
            user_id=current_user.id,
            audio_bytes=audio_bytes,
            mime_type=audio.content_type,
            filename=audio.filename,
            app_language_code=app_language_code,
            speech_locale_id=speech_locale_id,
        )
    except ValueError as exc:
        LOGGER.warning(
            "portfolio_voice_audio_bad_request detail=%s user_id=%s audio_filename=%s audio_mime_type=%s audio_size_bytes=%s",
            str(exc),
            current_user.id,
            audio.filename,
            audio.content_type,
            len(audio_bytes) if "audio_bytes" in locals() else 0,
            extra={
                "user_id": current_user.id,
                "app_language_code": app_language_code,
                "speech_locale_id": speech_locale_id,
                "audio_filename": audio.filename,
                "audio_mime_type": audio.content_type,
                "audio_size_bytes": len(audio_bytes) if "audio_bytes" in locals() else 0,
                "detail": str(exc),
            },
        )
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except PortfolioVoiceNotConfiguredError as exc:
        LOGGER.warning(
            "portfolio_voice_audio_not_configured detail=%s user_id=%s audio_filename=%s audio_mime_type=%s audio_size_bytes=%s",
            str(exc),
            current_user.id,
            audio.filename,
            audio.content_type,
            len(audio_bytes) if "audio_bytes" in locals() else 0,
        )
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except PortfolioVoiceUnavailableError as exc:
        LOGGER.warning(
            "portfolio_voice_audio_unavailable detail=%s user_id=%s audio_filename=%s audio_mime_type=%s audio_size_bytes=%s",
            str(exc),
            current_user.id,
            audio.filename,
            audio.content_type,
            len(audio_bytes) if "audio_bytes" in locals() else 0,
        )
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except PortfolioVoiceInterpretationError as exc:
        LOGGER.warning(
            "portfolio_voice_audio_interpretation_failed detail=%s user_id=%s audio_filename=%s audio_mime_type=%s audio_size_bytes=%s",
            str(exc),
            current_user.id,
            audio.filename,
            audio.content_type,
            len(audio_bytes) if "audio_bytes" in locals() else 0,
            extra={
                "user_id": current_user.id,
                "app_language_code": app_language_code,
                "speech_locale_id": speech_locale_id,
                "audio_filename": audio.filename,
                "audio_mime_type": audio.content_type,
                "audio_size_bytes": len(audio_bytes) if "audio_bytes" in locals() else 0,
                "detail": str(exc),
            },
        )
        raise HTTPException(status_code=400, detail=str(exc)) from exc


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

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.deps import get_current_user
from app.models.entities import User
from app.schemas.dashboard import GameOverviewResponse
from app.services.dashboard_service import build_game_overview


router = APIRouter(prefix="/game", tags=["game"])


@router.get("/overview", response_model=GameOverviewResponse)
async def game_overview(current_user: User = Depends(get_current_user)) -> GameOverviewResponse:
    return build_game_overview(current_user)


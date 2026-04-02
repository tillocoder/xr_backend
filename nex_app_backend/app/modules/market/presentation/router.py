from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.deps import get_current_user
from app.models.entities import User
from app.schemas.dashboard import MarketOverviewResponse
from app.services.dashboard_service import build_market_overview


router = APIRouter(prefix="/market", tags=["market"])


@router.get("/overview", response_model=MarketOverviewResponse)
async def market_overview(current_user: User = Depends(get_current_user)) -> MarketOverviewResponse:
    return build_market_overview(current_user)


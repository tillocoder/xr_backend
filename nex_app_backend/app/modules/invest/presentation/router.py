from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.deps import get_current_user
from app.models.entities import User
from app.schemas.dashboard import InvestOverviewResponse
from app.services.dashboard_service import build_invest_overview


router = APIRouter(prefix="/invest", tags=["invest"])


@router.get("/overview", response_model=InvestOverviewResponse)
async def invest_overview(current_user: User = Depends(get_current_user)) -> InvestOverviewResponse:
    return build_invest_overview(current_user)


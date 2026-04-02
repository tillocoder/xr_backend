from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.deps import get_current_user
from app.models.entities import User
from app.schemas.dashboard import HomeBootstrapResponse
from app.schemas.profile import ProfileOverviewResponse
from app.services.dashboard_service import build_home_bootstrap, build_preview_user


router = APIRouter(prefix="/profile", tags=["profile"])


@router.get("/me", response_model=ProfileOverviewResponse)
async def profile_me(current_user: User = Depends(get_current_user)) -> ProfileOverviewResponse:
    return ProfileOverviewResponse(
        id=current_user.id,
        full_name=current_user.full_name,
        email=current_user.email,
        role=current_user.role,
        membership_label="Gold member",
        city="Toshkent",
        verified=True,
    )


@router.get("/bootstrap", response_model=HomeBootstrapResponse)
async def profile_bootstrap(current_user: User = Depends(get_current_user)) -> HomeBootstrapResponse:
    return build_home_bootstrap(current_user)


@router.get("/preview-bootstrap", response_model=HomeBootstrapResponse)
async def profile_preview_bootstrap() -> HomeBootstrapResponse:
    return build_home_bootstrap(build_preview_user())

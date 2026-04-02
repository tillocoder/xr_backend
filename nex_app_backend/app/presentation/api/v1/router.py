from __future__ import annotations

from fastapi import APIRouter

from app.modules.auth.presentation import router as auth_router
from app.modules.game.presentation import router as game_router
from app.modules.invest.presentation import router as invest_router
from app.modules.market.presentation import router as market_router
from app.modules.profile.presentation import router as profile_router


router = APIRouter()
router.include_router(auth_router)
router.include_router(profile_router)
router.include_router(market_router)
router.include_router(invest_router)
router.include_router(game_router)


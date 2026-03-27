from __future__ import annotations

from fastapi import APIRouter

from app.modules.account.presentation import router as account_router
from app.modules.community.presentation import router as community_router
from app.modules.learning.presentation import router as learning_router
from app.modules.legacy.presentation import router as legacy_router
from app.modules.realtime.presentation import router as realtime_router
from app.modules.signals.presentation import router as signals_router


router = APIRouter()
router.include_router(legacy_router)
router.include_router(community_router)
router.include_router(learning_router)
router.include_router(account_router)
router.include_router(signals_router)
router.include_router(realtime_router)

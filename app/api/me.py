from datetime import datetime
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, Depends, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, get_current_user
from app.db.session import get_db
from app.models.entities import CommunityProfile, User
from app.schemas.notification import (
    MarkNotificationsReadRequest,
    NotificationListResponse,
    PushTokenPayload,
)
from app.services.daily_reward_service import DailyRewardService, DailyRewardStatus
from app.services.membership_tiers import (
    MEMBERSHIP_TIER_FREE,
    is_paid_membership_tier,
)
from app.services.user_service import ensure_user_exists

router = APIRouter(prefix="/me", tags=["me"])


class MeHoldingsPayload(BaseModel):
    items: list[dict[str, Any]] = Field(default_factory=list)


class MeWalletsPayload(BaseModel):
    items: list[dict[str, Any]] = Field(default_factory=list)


class MeWatchlistPayload(BaseModel):
    symbols: list[str] = Field(default_factory=list)


class MeBootstrapPayload(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    settings: dict[str, Any] = Field(default_factory=dict)
    holdings: list[dict[str, Any]] = Field(default_factory=list)
    linked_wallets: list[dict[str, Any]] = Field(default_factory=list, alias="linkedWallets")
    watchlist: list[str] = Field(default_factory=list)


class DailyRewardStatusResponse(BaseModel):
    diamondsBalance: int
    currentStreak: int
    streakGoal: int
    rewardPerClaim: int
    rewardProCost: int
    rewardProDurationDays: int
    rewardProPostsPerDay: int
    paidProPostsPerDay: int
    canClaimNow: bool
    lastClaimedAt: datetime | None = None
    nextClaimAt: datetime | None = None
    rewardProExpiresAt: datetime | None = None
    membershipTier: str = MEMBERSHIP_TIER_FREE
    effectiveMembershipTier: str = MEMBERSHIP_TIER_FREE
    paidProActive: bool = False
    rewardProActive: bool = False
    effectiveProActive: bool = False
    rewardProRemainingSeconds: int = 0


class MembershipPlanResponse(BaseModel):
    code: str
    tier: str
    title: str
    subtitle: str
    durationMonths: int
    priceAmount: float
    currency: str
    displayPrice: str
    badgeLabel: str | None = None


class MembershipCatalogResponse(BaseModel):
    proPlans: list[MembershipPlanResponse] = Field(default_factory=list)
    legendPlans: list[MembershipPlanResponse] = Field(default_factory=list)


class MembershipPurchaseIntentRequest(BaseModel):
    tier: str
    planCode: str

    @field_validator("tier")
    @classmethod
    def _normalize_tier(cls, value: str) -> str:
        return value.strip().lower()


class MembershipPurchaseIntentResponse(BaseModel):
    checkoutToken: str
    tier: str
    planCode: str
    status: str
    title: str
    durationMonths: int
    priceAmount: float
    currency: str
    displayPrice: str
    message: str


def _daily_reward_service() -> DailyRewardService:
    return DailyRewardService()


async def _get_or_create_user(db: AsyncSession, user_id: str) -> tuple[User, bool]:
    user = await db.get(User, user_id)
    if user is None:
        return await ensure_user_exists(db, user_id), True
    return user, False


def _normalized_watchlist(symbols: list[str]) -> list[str]:
    return sorted({item.strip().upper() for item in symbols if item.strip()})


def _apply_settings_payload(user: User, payload: dict[str, Any]) -> bool:
    next_settings = dict(payload)
    current_settings = dict(user.settings_json if isinstance(user.settings_json, dict) else {})
    if current_settings == next_settings:
        return False
    user.settings_json = next_settings
    return True


def _apply_holdings_payload(user: User, payload: list[dict[str, Any]]) -> bool:
    next_holdings = list(payload)
    current_holdings = list(user.holdings_json if isinstance(user.holdings_json, list) else [])
    if current_holdings == next_holdings:
        return False
    user.holdings_json = next_holdings
    return True


def _apply_wallets_payload(user: User, payload: list[dict[str, Any]]) -> bool:
    next_wallets = list(payload)
    current_wallets = list(
        user.linked_wallets_json if isinstance(user.linked_wallets_json, list) else []
    )
    if current_wallets == next_wallets:
        return False
    user.linked_wallets_json = next_wallets
    return True


def _apply_watchlist_payload(user: User, payload: list[str]) -> bool:
    next_watchlist = _normalized_watchlist(payload)
    current_watchlist = list(
        user.watchlist_json if isinstance(user.watchlist_json, list) else []
    )
    if current_watchlist == next_watchlist:
        return False
    user.watchlist_json = next_watchlist
    return True


def _daily_reward_response(status: DailyRewardStatus) -> DailyRewardStatusResponse:
    return DailyRewardStatusResponse(
        diamondsBalance=status.diamonds_balance,
        currentStreak=status.current_streak,
        streakGoal=status.streak_goal,
        rewardPerClaim=status.reward_per_claim,
        rewardProCost=status.reward_pro_cost,
        rewardProDurationDays=status.reward_pro_duration_days,
        rewardProPostsPerDay=status.reward_pro_posts_per_day,
        paidProPostsPerDay=status.paid_pro_posts_per_day,
        canClaimNow=status.can_claim_now,
        lastClaimedAt=status.last_claimed_at,
        nextClaimAt=status.next_claim_at,
        rewardProExpiresAt=status.reward_pro_expires_at,
        membershipTier=status.membership_tier,
        effectiveMembershipTier=status.effective_membership_tier,
        paidProActive=status.paid_pro_active,
        rewardProActive=status.reward_pro_active,
        effectiveProActive=status.effective_pro_active,
        rewardProRemainingSeconds=status.reward_pro_remaining_seconds,
    )


def _membership_catalog() -> MembershipCatalogResponse:
    pro_monthly = 15000
    pro_plans = [
        MembershipPlanResponse(
            code="xr_pro_1m",
            tier="pro",
            title="XR Pro 1 month",
            subtitle="Community posting, premium avatar wave, stronger profile presence.",
            durationMonths=1,
            priceAmount=float(pro_monthly),
            currency="UZS",
            displayPrice="15 000 so'm",
            badgeLabel="STARTER",
        ),
        MembershipPlanResponse(
            code="xr_pro_3m",
            tier="pro",
            title="XR Pro 3 months",
            subtitle="Same Pro access for a focused quarter.",
            durationMonths=3,
            priceAmount=float(pro_monthly * 3),
            currency="UZS",
            displayPrice="45 000 so'm",
            badgeLabel="POPULAR",
        ),
        MembershipPlanResponse(
            code="xr_pro_12m",
            tier="pro",
            title="XR Pro 12 months",
            subtitle="A full year of Pro access without renewal pressure.",
            durationMonths=12,
            priceAmount=float(pro_monthly * 12),
            currency="UZS",
            displayPrice="180 000 so'm",
            badgeLabel="BEST VALUE",
        ),
    ]
    legend_plans = [
        MembershipPlanResponse(
            code="xr_legend_12m",
            tier="legend",
            title="XR Legend 12 months",
            subtitle="Top-tier annual legend membership with the most exclusive profile tier.",
            durationMonths=12,
            priceAmount=85.0,
            currency="USD",
            displayPrice="$85 / year",
            badgeLabel="LEGEND ONLY",
        ),
    ]
    return MembershipCatalogResponse(proPlans=pro_plans, legendPlans=legend_plans)


def _find_membership_plan(tier: str, plan_code: str) -> MembershipPlanResponse | None:
    catalog = _membership_catalog()
    normalized_tier = tier.strip().lower()
    normalized_code = plan_code.strip().lower()
    pool = catalog.proPlans if normalized_tier == "pro" else catalog.legendPlans if normalized_tier == "legend" else []
    for item in pool:
        if item.code.lower() == normalized_code:
            return item
    return None


@router.get("/bootstrap")
async def get_bootstrap(
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    user = await db.get(User, current_user.id)
    if user is None:
        user = await ensure_user_exists(db, current_user.id)
        await db.commit()
        await db.refresh(user)

    settings = dict(user.settings_json if isinstance(user.settings_json, dict) else {})
    linked_wallets = list(
        user.linked_wallets_json if isinstance(user.linked_wallets_json, list) else []
    )
    holdings = list(user.holdings_json if isinstance(user.holdings_json, list) else [])
    watchlist = sorted(
        {
            str(item).strip().upper()
            for item in (user.watchlist_json if isinstance(user.watchlist_json, list) else [])
            if str(item).strip()
        }
    )
    reward_status = await _daily_reward_service().get_status(db, user_id=current_user.id)
    return {
        "settings": settings,
        "holdings": holdings,
        "linkedWallets": linked_wallets,
        "watchlist": watchlist,
        "isPro": reward_status.effective_pro_active,
        "membershipTier": reward_status.membership_tier,
        "effectiveMembershipTier": reward_status.effective_membership_tier,
        "dailyReward": _daily_reward_response(reward_status).model_dump(mode="json"),
    }


@router.get("/daily-reward", response_model=DailyRewardStatusResponse)
async def get_daily_reward_status(
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> DailyRewardStatusResponse:
    reward_status = await _daily_reward_service().get_status(db, user_id=current_user.id)
    return _daily_reward_response(reward_status)


@router.post("/daily-reward/claim", response_model=DailyRewardStatusResponse)
async def claim_daily_reward(
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> DailyRewardStatusResponse:
    reward_status = await _daily_reward_service().claim(db, user_id=current_user.id)
    return _daily_reward_response(reward_status)


@router.get("/membership/offers", response_model=MembershipCatalogResponse)
async def get_membership_offers(
    current_user: CurrentUser = Depends(get_current_user),
) -> MembershipCatalogResponse:
    del current_user
    return _membership_catalog()


@router.post("/membership/purchase-intent", response_model=MembershipPurchaseIntentResponse)
async def create_membership_purchase_intent(
    payload: MembershipPurchaseIntentRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> MembershipPurchaseIntentResponse:
    plan = _find_membership_plan(payload.tier, payload.planCode)
    if plan is None:
        return MembershipPurchaseIntentResponse(
            checkoutToken="",
            tier=payload.tier,
            planCode=payload.planCode,
            status="invalid_plan",
            title="Unknown plan",
            durationMonths=0,
            priceAmount=0,
            currency="",
            displayPrice="",
            message="Selected membership plan was not found.",
        )
    user = await db.get(User, current_user.id)
    if user is None:
        user = await ensure_user_exists(db, current_user.id)
    user.membership_tier = plan.tier
    user.is_pro = is_paid_membership_tier(plan.tier)
    profile = await db.get(CommunityProfile, current_user.id)
    if profile is not None:
        profile.is_pro = user.is_pro
    await db.commit()
    return MembershipPurchaseIntentResponse(
        checkoutToken=f"checkout_{uuid4().hex}",
        tier=plan.tier,
        planCode=plan.code,
        status="activated_mock",
        title=plan.title,
        durationMonths=plan.durationMonths,
        priceAmount=plan.priceAmount,
        currency=plan.currency,
        displayPrice=plan.displayPrice,
        message=(
            f"{plan.title} is active for {current_user.id}. "
            "This backend currently applies the tier immediately so the app can be tested end-to-end."
        ),
    )


@router.put("/settings")
async def update_settings(
    payload: dict[str, Any],
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    user, created = await _get_or_create_user(db, current_user.id)
    changed = _apply_settings_payload(user, payload)
    if changed or created:
        await db.commit()
    return {"ok": True, "changed": changed}


@router.put("/holdings")
async def update_holdings(
    payload: MeHoldingsPayload,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    user, created = await _get_or_create_user(db, current_user.id)
    changed = _apply_holdings_payload(user, payload.items)
    if changed or created:
        await db.commit()
    return {"ok": True, "changed": changed}


@router.put("/wallets")
async def update_wallets(
    payload: MeWalletsPayload,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    user, created = await _get_or_create_user(db, current_user.id)
    changed = _apply_wallets_payload(user, payload.items)
    if changed or created:
        await db.commit()
    return {"ok": True, "changed": changed}


@router.put("/watchlist")
async def update_watchlist(
    payload: MeWatchlistPayload,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    user, created = await _get_or_create_user(db, current_user.id)
    changed = _apply_watchlist_payload(user, payload.symbols)
    if changed or created:
        await db.commit()
    return {"ok": True, "changed": changed}


@router.put("/bootstrap")
async def update_bootstrap(
    payload: MeBootstrapPayload,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    user, created = await _get_or_create_user(db, current_user.id)
    changed = False
    changed = _apply_settings_payload(user, payload.settings) or changed
    changed = _apply_holdings_payload(user, payload.holdings) or changed
    changed = _apply_wallets_payload(user, payload.linked_wallets) or changed
    changed = _apply_watchlist_payload(user, payload.watchlist) or changed
    if changed or created:
        await db.commit()
    return {"ok": True, "changed": changed}


@router.get("/notifications", response_model=NotificationListResponse)
async def get_notifications(
    request: Request,
    limit: int = 20,
    unread_only: bool = True,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> NotificationListResponse:
    service = request.app.state.notification_service
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
    service = request.app.state.notification_service
    await service.mark_read(db, user_id=current_user.id, ids=payload.ids)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/notifications/read-all", status_code=status.HTTP_204_NO_CONTENT, response_class=Response)
async def mark_all_notifications_read(
    request: Request,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Response:
    service = request.app.state.notification_service
    await service.mark_all_read(db, user_id=current_user.id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/push-token", status_code=status.HTTP_204_NO_CONTENT, response_class=Response)
async def register_push_token(
    payload: PushTokenPayload,
    request: Request,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Response:
    service = request.app.state.push_token_service
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
    service = request.app.state.push_token_service
    await service.unregister_token(
        db,
        user_id=current_user.id,
        token=payload.token,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)

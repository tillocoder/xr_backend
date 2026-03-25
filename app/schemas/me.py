from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.services.membership_tiers import MEMBERSHIP_TIER_FREE


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
    linked_wallets: list[dict[str, Any]] = Field(
        default_factory=list,
        validation_alias="linkedWallets",
        serialization_alias="linkedWallets",
    )
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

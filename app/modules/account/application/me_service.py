from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.entities import User
from app.schemas.me import (
    DailyRewardStatusResponse,
    MeBootstrapPayload,
    MembershipCatalogResponse,
    PortfolioVoiceCommandResponse,
)
from app.services.daily_reward_service import DailyRewardService, DailyRewardStatus
from app.services.membership_catalog_service import MembershipCatalogService
from app.services.portfolio_voice_command_service import PortfolioVoiceCommandService
from app.services.user_service import ensure_user_exists


class MeService:
    def __init__(
        self,
        daily_reward_service: DailyRewardService | None = None,
        membership_catalog_service: MembershipCatalogService | None = None,
        portfolio_voice_command_service: PortfolioVoiceCommandService | None = None,
    ) -> None:
        self._daily_reward_service = daily_reward_service or DailyRewardService()
        self._membership_catalog = membership_catalog_service or MembershipCatalogService()
        self._portfolio_voice_command_service = (
            portfolio_voice_command_service or PortfolioVoiceCommandService()
        )

    async def get_bootstrap(
        self,
        db: AsyncSession,
        *,
        user_id: str,
    ) -> dict[str, Any]:
        user, reward_status = await self._daily_reward_service.get_status_with_user(
            db,
            user_id=user_id,
        )
        return {
            "settings": dict(user.settings_json if isinstance(user.settings_json, dict) else {}),
            "holdings": list(user.holdings_json if isinstance(user.holdings_json, list) else []),
            "linkedWallets": list(
                user.linked_wallets_json if isinstance(user.linked_wallets_json, list) else []
            ),
            "watchlist": self._normalized_watchlist(
                [str(item) for item in (user.watchlist_json if isinstance(user.watchlist_json, list) else [])]
            ),
            "isPro": reward_status.effective_pro_active,
            "membershipTier": reward_status.membership_tier,
            "effectiveMembershipTier": reward_status.effective_membership_tier,
            "dailyReward": self._daily_reward_response(reward_status).model_dump(mode="json"),
        }

    async def get_daily_reward_status(
        self,
        db: AsyncSession,
        *,
        user_id: str,
    ) -> DailyRewardStatusResponse:
        reward_status = await self._daily_reward_service.get_status(db, user_id=user_id)
        return self._daily_reward_response(reward_status)

    async def claim_daily_reward(
        self,
        db: AsyncSession,
        *,
        user_id: str,
    ) -> DailyRewardStatusResponse:
        reward_status = await self._daily_reward_service.claim(db, user_id=user_id)
        return self._daily_reward_response(reward_status)

    def get_membership_catalog(self) -> MembershipCatalogResponse:
        return self._membership_catalog.get_catalog()

    async def update_settings(
        self,
        db: AsyncSession,
        *,
        user_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        user, created = await self._get_or_create_user(db, user_id)
        changed = self._apply_settings_payload(user, payload)
        if changed or created:
            await db.commit()
        return {"ok": True, "changed": changed}

    async def update_holdings(
        self,
        db: AsyncSession,
        *,
        user_id: str,
        items: list[dict[str, Any]],
    ) -> dict[str, Any]:
        user, created = await self._get_or_create_user(db, user_id)
        changed = self._apply_holdings_payload(user, items)
        if changed or created:
            await db.commit()
        return {"ok": True, "changed": changed}

    async def update_wallets(
        self,
        db: AsyncSession,
        *,
        user_id: str,
        items: list[dict[str, Any]],
    ) -> dict[str, Any]:
        user, created = await self._get_or_create_user(db, user_id)
        changed = self._apply_wallets_payload(user, items)
        if changed or created:
            await db.commit()
        return {"ok": True, "changed": changed}

    async def update_watchlist(
        self,
        db: AsyncSession,
        *,
        user_id: str,
        symbols: list[str],
    ) -> dict[str, Any]:
        user, created = await self._get_or_create_user(db, user_id)
        changed = self._apply_watchlist_payload(user, symbols)
        if changed or created:
            await db.commit()
        return {"ok": True, "changed": changed}

    async def update_bootstrap(
        self,
        db: AsyncSession,
        *,
        user_id: str,
        payload: MeBootstrapPayload,
    ) -> dict[str, Any]:
        user, created = await self._get_or_create_user(db, user_id)
        changed = False
        changed = self._apply_settings_payload(user, payload.settings) or changed
        changed = self._apply_holdings_payload(user, payload.holdings) or changed
        changed = self._apply_wallets_payload(user, payload.linked_wallets) or changed
        changed = self._apply_watchlist_payload(user, payload.watchlist) or changed
        if changed or created:
            await db.commit()
        return {"ok": True, "changed": changed}

    async def process_portfolio_voice_command(
        self,
        db: AsyncSession,
        *,
        user_id: str,
        transcript: str,
        app_language_code: str,
        speech_locale_id: str | None = None,
    ) -> PortfolioVoiceCommandResponse:
        del user_id
        return await self._portfolio_voice_command_service.interpret_transcript(
            db,
            transcript=transcript,
            app_language_code=app_language_code,
            speech_locale_id=speech_locale_id,
        )

    async def process_portfolio_voice_audio(
        self,
        db: AsyncSession,
        *,
        user_id: str,
        audio_bytes: bytes,
        mime_type: str | None,
        filename: str | None,
        app_language_code: str,
        speech_locale_id: str | None = None,
    ) -> PortfolioVoiceCommandResponse:
        del user_id
        return await self._portfolio_voice_command_service.interpret_audio(
            db,
            audio_bytes=audio_bytes,
            mime_type=mime_type,
            filename=filename,
            app_language_code=app_language_code,
            speech_locale_id=speech_locale_id,
        )

    async def _get_or_create_user(self, db: AsyncSession, user_id: str) -> tuple[User, bool]:
        user = await db.get(User, user_id)
        if user is None:
            return await ensure_user_exists(db, user_id), True
        return user, False

    def _apply_settings_payload(self, user: User, payload: dict[str, Any]) -> bool:
        next_settings = dict(payload)
        current_settings = dict(user.settings_json if isinstance(user.settings_json, dict) else {})
        if current_settings == next_settings:
            return False
        user.settings_json = next_settings
        return True

    def _apply_holdings_payload(self, user: User, payload: list[dict[str, Any]]) -> bool:
        next_holdings = list(payload)
        current_holdings = list(user.holdings_json if isinstance(user.holdings_json, list) else [])
        if current_holdings == next_holdings:
            return False
        user.holdings_json = next_holdings
        return True

    def _apply_wallets_payload(self, user: User, payload: list[dict[str, Any]]) -> bool:
        next_wallets = list(payload)
        current_wallets = list(
            user.linked_wallets_json if isinstance(user.linked_wallets_json, list) else []
        )
        if current_wallets == next_wallets:
            return False
        user.linked_wallets_json = next_wallets
        return True

    def _apply_watchlist_payload(self, user: User, payload: list[str]) -> bool:
        next_watchlist = self._normalized_watchlist(payload)
        current_watchlist = list(user.watchlist_json if isinstance(user.watchlist_json, list) else [])
        if current_watchlist == next_watchlist:
            return False
        user.watchlist_json = next_watchlist
        return True

    def _normalized_watchlist(self, symbols: list[str]) -> list[str]:
        return sorted({item.strip().upper() for item in symbols if item.strip()})

    def _daily_reward_response(self, status: DailyRewardStatus) -> DailyRewardStatusResponse:
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

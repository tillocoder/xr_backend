from __future__ import annotations

from app.schemas.me import MembershipCatalogResponse, MembershipPlanResponse
from app.services.membership_tiers import MEMBERSHIP_TIER_LEGEND, MEMBERSHIP_TIER_PRO


class MembershipCatalogService:
    def __init__(self) -> None:
        self._catalog = MembershipCatalogResponse(
            proPlans=[
                MembershipPlanResponse(
                    code="xr_pro_1m",
                    tier=MEMBERSHIP_TIER_PRO,
                    title="XR Pro 1 oy",
                    subtitle="Premium signal va smart alertlar uchun tez start.",
                    durationMonths=1,
                    priceAmount=15000,
                    currency="UZS",
                    displayPrice="15 000 so'm",
                    badgeLabel="START",
                ),
                MembershipPlanResponse(
                    code="xr_pro_3m",
                    tier=MEMBERSHIP_TIER_PRO,
                    title="XR Pro 3 oy",
                    subtitle="Barqaror premium ritm, oylikdan qulayroq narx bilan.",
                    durationMonths=3,
                    priceAmount=39000,
                    currency="UZS",
                    displayPrice="39 000 so'm",
                    badgeLabel="SAVE",
                ),
                MembershipPlanResponse(
                    code="xr_pro_12m",
                    tier=MEMBERSHIP_TIER_PRO,
                    title="XR Pro 12 oy",
                    subtitle="Eng qulay yillik Pro narxi va uzoq premium access.",
                    durationMonths=12,
                    priceAmount=149000,
                    currency="UZS",
                    displayPrice="149 000 so'm",
                    badgeLabel="BEST",
                ),
            ],
            legendPlans=[
                MembershipPlanResponse(
                    code="xr_legend_1m",
                    tier=MEMBERSHIP_TIER_LEGEND,
                    title="XR Legend 1 oy",
                    subtitle="Legend tajribasini tez yoqib ko'rish uchun qisqa premium.",
                    durationMonths=1,
                    priceAmount=39000,
                    currency="UZS",
                    displayPrice="39 000 so'm",
                    badgeLabel="LEGEND",
                ),
                MembershipPlanResponse(
                    code="xr_legend_3m",
                    tier=MEMBERSHIP_TIER_LEGEND,
                    title="XR Legend 3 oy",
                    subtitle="Legend identity va premium ko'rinish uchun kuchli paket.",
                    durationMonths=3,
                    priceAmount=99000,
                    currency="UZS",
                    displayPrice="99 000 so'm",
                    badgeLabel="POPULAR",
                ),
                MembershipPlanResponse(
                    code="xr_legend_12m",
                    tier=MEMBERSHIP_TIER_LEGEND,
                    title="XR Legend 12 oy",
                    subtitle="Eng yuqori tierni to'liq yil davomida faol ushlab turadi.",
                    durationMonths=12,
                    priceAmount=349000,
                    currency="UZS",
                    displayPrice="349 000 so'm",
                    badgeLabel="BEST",
                ),
            ],
        )

    def get_catalog(self) -> MembershipCatalogResponse:
        return self._catalog.model_copy(deep=True)

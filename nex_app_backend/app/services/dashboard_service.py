from __future__ import annotations

from app.models.entities import User
from app.schemas.dashboard import (
    ActivityEntryResponse,
    BattleLobbyResponse,
    BattleSeatResponse,
    GameModeResponse,
    GameOverviewResponse,
    HomeBootstrapResponse,
    InvestmentPlanResponse,
    InvestOverviewResponse,
    MarketOfferResponse,
    MarketOverviewResponse,
    MarketSignalResponse,
    PortfolioOverviewResponse,
    ProfileStatResponse,
    UserProfileResponse,
    WalletCardResponse,
)


def build_market_overview(user: User) -> MarketOverviewResponse:
    return MarketOverviewResponse(
        balance=18_450_000,
        cashback_rate=3.8,
        tier="Prime Wallet",
        top_up_presets=[100_000, 250_000, 500_000, 1_000_000],
    )


def build_invest_overview(user: User) -> InvestOverviewResponse:
    return InvestOverviewResponse(
        total_value=27_800_000,
        invested_capital=21_900_000,
        liquid_reserve=5_900_000,
        monthly_growth=14.6,
    )


def build_game_overview(user: User) -> GameOverviewResponse:
    return GameOverviewResponse(
        season_label="Season 03",
        live_pool=2_460_000,
        recommended_stake=100_000,
        registered_players=8,
    )


def build_home_bootstrap(user: User) -> HomeBootstrapResponse:
    market_overview = build_market_overview(user)
    invest_overview = build_invest_overview(user)
    game_overview = build_game_overview(user)

    return HomeBootstrapResponse(
        wallet_card=WalletCardResponse(
            owner_name=user.full_name,
            masked_number="8600 19** **** 4821",
            tier=market_overview.tier,
            balance=market_overview.balance,
            spending_limit=30_000_000,
            spent_this_month=12_400_000,
            investable_balance=6_200_000,
            cashback_rate=market_overview.cashback_rate,
        ),
        quick_top_up_presets=market_overview.top_up_presets,
        market_signals=[
            MarketSignalResponse(
                title="Balans faol",
                body="Kartani tez toldirish va auto top-up uchun backend tayyor.",
                badge="LIVE",
            ),
            MarketSignalResponse(
                title="Investga otish oson",
                body="Bosh mablagni bir klikda invest bolimiga ajratish mumkin.",
                badge="SMART",
            ),
            MarketSignalResponse(
                title="Battle ready",
                body="Game bolimidagi stake user walletidan yechiladi.",
                badge="ARENA",
            ),
        ],
        market_offers=[
            MarketOfferResponse(
                title="Auto top-up",
                subtitle="Balans 100 000 dan tushsa avtomatik toldirish.",
                reward_label="No fee",
                eta_label="30 sec setup",
            ),
            MarketOfferResponse(
                title="Spending shield",
                subtitle="Oy boyicha limit va xavfsiz tranzaksiya filteri.",
                reward_label="Secure",
                eta_label="Instant",
            ),
            MarketOfferResponse(
                title="Split wallet",
                subtitle="Asosiy balans, invest va battle uchun alohida savatcha.",
                reward_label="3 pockets",
                eta_label="Preview",
            ),
        ],
        portfolio=PortfolioOverviewResponse(
            total_value=invest_overview.total_value,
            invested_capital=invest_overview.invested_capital,
            profit_today=240_000,
            liquid_reserve=invest_overview.liquid_reserve,
            monthly_growth=invest_overview.monthly_growth,
            yearly_projection=38.2,
        ),
        investment_plans=[
            InvestmentPlanResponse(
                name="Stable Flow",
                summary="Past risk, doimiy cashflow va kundalik monitoring.",
                risk_label="Low risk",
                duration_label="30-90 kun",
                min_stake=500_000,
                projected_yield=12.0,
                featured=False,
            ),
            InvestmentPlanResponse(
                name="Growth Wave",
                summary="Agresivroq strategiya, foyda va drawdown balansda.",
                risk_label="Medium risk",
                duration_label="90-180 kun",
                min_stake=1_500_000,
                projected_yield=24.0,
                featured=True,
            ),
            InvestmentPlanResponse(
                name="Battle Reserve",
                summary="Game stake uchun alohida fond va auto reinvest qoidalari.",
                risk_label="Dynamic",
                duration_label="Flexible",
                min_stake=250_000,
                projected_yield=18.5,
                featured=False,
            ),
        ],
        battle_lobby=BattleLobbyResponse(
            title="10-player battle",
            season_label=game_overview.season_label,
            mode_description="Tezkor refleks va qisqa strategiya raundi.",
            round_duration="90 soniya",
            min_stake=10_000,
            max_stake=1_000_000_000,
            recommended_stake=game_overview.recommended_stake,
            live_pool=game_overview.live_pool,
            registered_players=game_overview.registered_players,
            seats=[
                BattleSeatResponse(
                    label="AK",
                    status_label="Ready",
                    is_filled=True,
                    accent_color_value=0xFF53D3A7,
                    score=92,
                ),
                BattleSeatResponse(
                    label="NZ",
                    status_label="Ready",
                    is_filled=True,
                    accent_color_value=0xFFF2BB5E,
                    score=88,
                ),
                BattleSeatResponse(
                    label="SH",
                    status_label="Sync",
                    is_filled=True,
                    accent_color_value=0xFF82A8FF,
                    score=84,
                ),
                BattleSeatResponse(
                    label="UM",
                    status_label="Ready",
                    is_filled=True,
                    accent_color_value=0xFFFF8E8E,
                    score=79,
                ),
                BattleSeatResponse(
                    label="KD",
                    status_label="Ready",
                    is_filled=True,
                    accent_color_value=0xFF83E8D0,
                    score=74,
                ),
                BattleSeatResponse(
                    label="MR",
                    status_label="Queue",
                    is_filled=True,
                    accent_color_value=0xFFE0C878,
                    score=70,
                ),
                BattleSeatResponse(
                    label="LV",
                    status_label="Ready",
                    is_filled=True,
                    accent_color_value=0xFFA9C2FF,
                    score=67,
                ),
                BattleSeatResponse(
                    label="TS",
                    status_label="Queue",
                    is_filled=True,
                    accent_color_value=0xFF62E0B2,
                    score=64,
                ),
                BattleSeatResponse(
                    label="+",
                    status_label="Bosh slot",
                    is_filled=False,
                    accent_color_value=0xFF3B4C5C,
                    score=0,
                ),
                BattleSeatResponse(
                    label="+",
                    status_label="Bosh slot",
                    is_filled=False,
                    accent_color_value=0xFF3B4C5C,
                    score=0,
                ),
            ],
        ),
        game_modes=[
            GameModeResponse(
                title="Market Rush",
                subtitle="Narx signaliga qarab tezkor qaror oyini.",
                prize_label="Pot split",
                duration_label="3 round",
            ),
            GameModeResponse(
                title="Tap Arena",
                subtitle="Refleks va aniqlik kombinatsiyasi.",
                prize_label="Winner takes more",
                duration_label="90 sec",
            ),
            GameModeResponse(
                title="Memory Grid",
                subtitle="Qisqa muddatli pattern xotira battle varianti.",
                prize_label="Rank bonus",
                duration_label="2 min",
            ),
        ],
        profile=UserProfileResponse(
            full_name=user.full_name,
            handle=_build_handle(user),
            membership_label="Gold member",
            city="Toshkent",
            verified=True,
            cards_count=2,
            win_rate=68,
            referral_count=14,
            stats=[
                ProfileStatResponse(
                    label="Battle winrate",
                    value="68%",
                    caption="Oxirgi 30 kun",
                ),
                ProfileStatResponse(
                    label="Invest ROI",
                    value="+14.6%",
                    caption="Oylik trend",
                ),
                ProfileStatResponse(
                    label="Auto top-up",
                    value="On",
                    caption="Threshold 100K",
                ),
            ],
            recent_activity=[
                ActivityEntryResponse(
                    title="Kartaga top-up",
                    subtitle="Wallet balansiga 500 000 UZS qoshildi",
                    time_label="Bugun 14:24",
                ),
                ActivityEntryResponse(
                    title="Battle registration",
                    subtitle="Market Rush lobbysi uchun 100 000 UZS stake tanlandi",
                    time_label="Bugun 13:10",
                ),
                ActivityEntryResponse(
                    title="Invest transfer",
                    subtitle="Growth Wave fondiga 1 500 000 UZS ajratildi",
                    time_label="Kecha 22:48",
                ),
            ],
        ),
    )


def build_preview_user() -> User:
    return User(
        id="preview-user",
        email="preview@nex.app",
        full_name="Nex Preview",
        hashed_password="preview-only",
        role="preview",
        is_active=True,
    )


def _build_handle(user: User) -> str:
    if user.email:
        local_part = user.email.split("@", maxsplit=1)[0].replace(".", "_")
        return f"@{local_part}"
    return "@nex_user"

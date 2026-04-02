from __future__ import annotations

from pydantic import BaseModel


class WalletCardResponse(BaseModel):
    owner_name: str
    masked_number: str
    tier: str
    balance: int
    spending_limit: int
    spent_this_month: int
    investable_balance: int
    cashback_rate: float


class MarketOverviewResponse(BaseModel):
    balance: int
    cashback_rate: float
    tier: str
    top_up_presets: list[int]


class MarketSignalResponse(BaseModel):
    title: str
    body: str
    badge: str


class MarketOfferResponse(BaseModel):
    title: str
    subtitle: str
    reward_label: str
    eta_label: str


class InvestOverviewResponse(BaseModel):
    total_value: int
    invested_capital: int
    liquid_reserve: int
    monthly_growth: float


class PortfolioOverviewResponse(BaseModel):
    total_value: int
    invested_capital: int
    profit_today: int
    liquid_reserve: int
    monthly_growth: float
    yearly_projection: float


class InvestmentPlanResponse(BaseModel):
    name: str
    summary: str
    risk_label: str
    duration_label: str
    min_stake: int
    projected_yield: float
    featured: bool


class GameOverviewResponse(BaseModel):
    season_label: str
    live_pool: int
    recommended_stake: int
    registered_players: int


class BattleSeatResponse(BaseModel):
    label: str
    status_label: str
    is_filled: bool
    accent_color_value: int
    score: int


class BattleLobbyResponse(BaseModel):
    title: str
    season_label: str
    mode_description: str
    round_duration: str
    min_stake: int
    max_stake: int
    recommended_stake: int
    live_pool: int
    registered_players: int
    seats: list[BattleSeatResponse]


class GameModeResponse(BaseModel):
    title: str
    subtitle: str
    prize_label: str
    duration_label: str


class ProfileStatResponse(BaseModel):
    label: str
    value: str
    caption: str


class ActivityEntryResponse(BaseModel):
    title: str
    subtitle: str
    time_label: str


class UserProfileResponse(BaseModel):
    full_name: str
    handle: str
    membership_label: str
    city: str
    verified: bool
    cards_count: int
    win_rate: int
    referral_count: int
    stats: list[ProfileStatResponse]
    recent_activity: list[ActivityEntryResponse]


class HomeBootstrapResponse(BaseModel):
    wallet_card: WalletCardResponse
    quick_top_up_presets: list[int]
    market_signals: list[MarketSignalResponse]
    market_offers: list[MarketOfferResponse]
    portfolio: PortfolioOverviewResponse
    investment_plans: list[InvestmentPlanResponse]
    battle_lobby: BattleLobbyResponse
    game_modes: list[GameModeResponse]
    profile: UserProfileResponse

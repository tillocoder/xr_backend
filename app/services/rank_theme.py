from __future__ import annotations

from app.services.membership_tiers import MEMBERSHIP_TIER_LEGEND, MEMBERSHIP_TIER_PRO

RANK_THEME_CLASSIC = "classic"
RANK_THEME_NAQSH = "naqsh"
RANK_THEME_BUTTERFLY = "butterfly"
RANK_THEME_TIKAN = "tikan"
RANK_THEME_GOLD_RING = "gold_ring"

RANK_THEME_VALUES = {
    RANK_THEME_CLASSIC,
    RANK_THEME_NAQSH,
    RANK_THEME_BUTTERFLY,
    RANK_THEME_TIKAN,
    RANK_THEME_GOLD_RING,
}

_RANK_THEME_ALIASES = {
    "default": RANK_THEME_CLASSIC,
    "wave": RANK_THEME_CLASSIC,
    "wawe": RANK_THEME_CLASSIC,
    "legend": RANK_THEME_CLASSIC,
    "ornate": RANK_THEME_NAQSH,
    "wawe_naqsh": RANK_THEME_NAQSH,
    "wave_naqsh": RANK_THEME_NAQSH,
    "butterfly_black": RANK_THEME_BUTTERFLY,
    "wawe_butterfly": RANK_THEME_BUTTERFLY,
    "wave_butterfly": RANK_THEME_BUTTERFLY,
    "thorn": RANK_THEME_TIKAN,
    "spike": RANK_THEME_TIKAN,
    "wawe_tikan": RANK_THEME_TIKAN,
    "wave_tikan": RANK_THEME_TIKAN,
    "goldring": RANK_THEME_GOLD_RING,
    "gold-ring": RANK_THEME_GOLD_RING,
}

_PRO_RANK_THEMES = (
    RANK_THEME_TIKAN,
    RANK_THEME_NAQSH,
)

_LEGEND_RANK_THEMES = (
    RANK_THEME_GOLD_RING,
    RANK_THEME_BUTTERFLY,
    RANK_THEME_NAQSH,
    RANK_THEME_TIKAN,
)


def normalize_rank_theme(value: object, *, fallback: str = RANK_THEME_CLASSIC) -> str:
    normalized = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    normalized = _RANK_THEME_ALIASES.get(normalized, normalized)
    if normalized in RANK_THEME_VALUES:
        return normalized
    return fallback


def default_rank_theme_for_membership(membership_tier: object) -> str:
    tier = str(membership_tier or "").strip().lower()
    if tier == MEMBERSHIP_TIER_LEGEND:
        return RANK_THEME_GOLD_RING
    if tier == MEMBERSHIP_TIER_PRO:
        return RANK_THEME_TIKAN
    return RANK_THEME_CLASSIC


def allowed_rank_themes_for_membership(membership_tier: object) -> tuple[str, ...]:
    tier = str(membership_tier or "").strip().lower()
    if tier == MEMBERSHIP_TIER_LEGEND:
        return _LEGEND_RANK_THEMES
    if tier == MEMBERSHIP_TIER_PRO:
        return _PRO_RANK_THEMES
    return (RANK_THEME_CLASSIC,)


def coerce_rank_theme_for_membership(*, value: object, membership_tier: object) -> str:
    normalized = normalize_rank_theme(value)
    allowed = allowed_rank_themes_for_membership(membership_tier)
    if normalized in allowed:
        return normalized
    return default_rank_theme_for_membership(membership_tier)


def resolve_rank_theme(
    *,
    user_rank_theme: object,
    profile_rank_theme: object,
    membership_tier: object,
) -> str:
    explicit = normalize_rank_theme(
        profile_rank_theme or user_rank_theme,
        fallback="",
    )
    if explicit:
        return coerce_rank_theme_for_membership(
            value=explicit,
            membership_tier=membership_tier,
        )
    return default_rank_theme_for_membership(membership_tier)

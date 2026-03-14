from __future__ import annotations

from typing import Final


MEMBERSHIP_TIER_FREE: Final[str] = "free"
MEMBERSHIP_TIER_PRO: Final[str] = "pro"
MEMBERSHIP_TIER_LEGEND: Final[str] = "legend"

_MEMBERSHIP_TIER_PRIORITIES: Final[dict[str, int]] = {
    MEMBERSHIP_TIER_FREE: 0,
    MEMBERSHIP_TIER_PRO: 1,
    MEMBERSHIP_TIER_LEGEND: 2,
}


def normalize_membership_tier(value: object) -> str:
    normalized = str(value or "").strip().lower()
    if normalized in _MEMBERSHIP_TIER_PRIORITIES:
        return normalized
    return MEMBERSHIP_TIER_FREE


def membership_tier_priority(value: object) -> int:
    return _MEMBERSHIP_TIER_PRIORITIES[normalize_membership_tier(value)]


def is_paid_membership_tier(value: object) -> bool:
    return normalize_membership_tier(value) in {
        MEMBERSHIP_TIER_PRO,
        MEMBERSHIP_TIER_LEGEND,
    }

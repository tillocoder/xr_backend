from __future__ import annotations

from datetime import datetime, timezone

from fastapi import HTTPException, status
from sqlalchemy.exc import IntegrityError

from app.models.entities import Comment, CommunityProfile, Post, User
from app.schemas.community import (
    CommunityCommentResponse,
    CommunityPollResponse,
    CommunityPostResponse,
    CommunityProfileResponse,
)
from app.services.daily_reward_service import DailyRewardService
from app.services.membership_tiers import MEMBERSHIP_TIER_LEGEND, MEMBERSHIP_TIER_PRO


REACTION_KEY_BY_CODE = {
    1: "bullish_up",
    2: "bearish_down",
    3: "laugh",
    4: "sad",
    5: "cry",
    6: "ok",
    7: "zor",
}
REACTION_CODES = tuple(REACTION_KEY_BY_CODE.keys())
REACTION_KEYS = tuple(REACTION_KEY_BY_CODE.values())
REACTION_CODE_ALIASES = {
    "bullish_up": 1,
    "up": 1,
    "bull": 1,
    "trending_up": 1,
    "bearish_down": 2,
    "down": 2,
    "bear": 2,
    "trending_down": 2,
    "laugh": 3,
    "haha": 3,
    "laughing": 3,
    "sad": 4,
    "cry": 5,
    "crying": 5,
    "ok": 6,
    "like": 6,
    "liked": 6,
    "thumbs_up": 6,
    "thumbsup": 6,
    "zor": 7,
    "fire": 7,
}


def empty_reaction_counts() -> dict[int, int]:
    return {code: 0 for code in REACTION_CODES}


def normalize_symbols(raw_values: list[object]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for raw in raw_values:
        expanded = str(raw or "").replace("#", " ").upper()
        expanded = expanded.replace("/", " ").replace("|", " ").replace(",", " ")
        for part in expanded.split():
            token = "".join(char for char in part if char.isalnum()).strip()
            if not token or len(token) > 10 or token in seen:
                continue
            seen.add(token)
            out.append(token)
            if len(out) >= 6:
                return out
    return out


def normalize_symbol(raw: object) -> str | None:
    values = normalize_symbols([raw])
    return values[0] if values else None


def normalize_market_bias(raw: object) -> str | None:
    value = str(raw or "").strip().lower()
    if value in {"bullish", "bearish"}:
        return value
    return None


def normalize_reaction_code(raw: object) -> int | None:
    if isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        return raw if raw in REACTION_CODES else None
    value = str(raw or "").strip().lower()
    if not value:
        return None
    numeric = int(value) if value.isdigit() else None
    if numeric in REACTION_CODES:
        return numeric
    return REACTION_CODE_ALIASES.get(value)


def normalize_reaction_key(raw: object) -> str | None:
    code = normalize_reaction_code(raw)
    return REACTION_KEY_BY_CODE.get(code) if code is not None else None


def reaction_code_to_storage(raw: object) -> str | None:
    code = normalize_reaction_code(raw)
    return str(code) if code is not None else None


def normalize_display_name(raw: object) -> str:
    cleaned = " ".join(str(raw or "").strip().split())
    return cleaned[:32].rstrip()


def normalize_username(raw: object) -> str:
    value = "".join(
        char
        for char in str(raw or "").strip().lower()
        if char.isalnum() or char in {"_", "."}
    )
    return value[:24]


def fallback_username(seed: str, *, uid: str) -> str:
    normalized = normalize_username(seed)
    if normalized:
        return normalized
    compact_uid = uid.strip().lower()
    return compact_uid[:12] if len(compact_uid) > 12 else compact_uid


def is_profile_username_conflict(error: IntegrityError) -> bool:
    detail = str(error.orig).lower()
    return (
        "community_profiles_username" in detail
        or "ix_community_profiles_username" in detail
        or 'key (username)=' in detail
    )


def normalize_short_text(raw: object, *, max_length: int) -> str:
    cleaned = " ".join(str(raw or "").strip().split())
    return cleaned[:max_length].rstrip()


def normalize_social_accounts(raw: dict[str, str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for key, value in raw.items():
        normalized_key = key.strip().lower()
        normalized_value = normalize_short_text(value, max_length=64)
        if normalized_key and normalized_value:
            out[normalized_key] = normalized_value
    return out


def nullable_text(raw: object) -> str | None:
    value = str(raw or "").strip()
    return value or None


def normalize_media_reference(raw: object) -> str | None:
    value = nullable_text(raw)
    if value is None:
        return None
    if value.startswith("/media/"):
        return value
    marker = value.find("/media/")
    if marker >= 0:
        return value[marker:]
    return value


def public_media_url(raw: object, public_base_url: str | None) -> str | None:
    value = nullable_text(raw)
    if value is None:
        return None
    normalized_base = (public_base_url or "").strip().rstrip("/")
    if value.startswith("/media/"):
        return f"{normalized_base}{value}" if normalized_base else value
    marker = value.find("/media/")
    if marker >= 0:
        relative = value[marker:]
        return f"{normalized_base}{relative}" if normalized_base else relative
    return value


def parse_datetime(raw: object) -> datetime | None:
    if raw is None:
        return None
    if isinstance(raw, datetime):
        return raw if raw.tzinfo is not None else raw.replace(tzinfo=timezone.utc)
    value = str(raw).strip()
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)


def non_negative_int(raw: object) -> int:
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return 0
    return value if value > 0 else 0


def to_float(raw: object) -> float:
    try:
        return float(raw)
    except (TypeError, ValueError):
        return 0.0


def holding_summaries_from_json(raw: object) -> dict[str, dict[str, object]]:
    holdings = list(raw if isinstance(raw, list) else [])
    grouped: dict[str, dict[str, object]] = {}
    for item in holdings:
        if not isinstance(item, dict):
            continue
        symbol = normalize_symbol(item.get("symbol"))
        amount = to_float(item.get("amount"))
        buy_price = to_float(item.get("buyPrice"))
        entry_date = parse_datetime(item.get("buyAt")) or parse_datetime(item.get("createdAt"))
        if symbol is None or amount <= 0 or entry_date is None:
            continue
        total_cost = amount * buy_price
        existing = grouped.get(symbol)
        if existing is None:
            grouped[symbol] = {"amount": amount, "totalCost": total_cost, "entryDate": entry_date}
            continue
        existing["amount"] = float(existing["amount"]) + amount
        existing["totalCost"] = float(existing["totalCost"]) + total_cost
        if entry_date < existing["entryDate"]:
            existing["entryDate"] = entry_date
    out: dict[str, dict[str, object]] = {}
    for symbol, item in grouped.items():
        amount = float(item["amount"])
        out[symbol] = {
            "amount": amount,
            "avgBuyPrice": float(item["totalCost"]) / amount if amount > 0 else 0.0,
            "entryDate": item["entryDate"],
        }
    return out


def profile_score(profile: CommunityProfileResponse, tokens: list[str]) -> tuple[int, int]:
    username = profile.username.lower()
    display_name = profile.displayName.lower()
    score = 0
    for token in tokens:
        if username == token or display_name == token:
            score += 50
        elif username.startswith(token) or display_name.startswith(token):
            score += 22
        elif token in username or token in display_name:
            score += 8
    if profile.membershipTier == MEMBERSHIP_TIER_LEGEND:
        score += 6
    elif profile.membershipTier == MEMBERSHIP_TIER_PRO:
        score += 3
    elif profile.isPro:
        score += 1
    return score, len(display_name)


class CommunityResponseFactory:
    def __init__(
        self,
        *,
        daily_rewards: DailyRewardService,
        public_base_url: str | None,
    ) -> None:
        self._daily_rewards = daily_rewards
        self._public_base_url = (public_base_url or "").strip().rstrip("/")

    def profile_response(
        self,
        user: User,
        profile: CommunityProfile | None,
    ) -> CommunityProfileResponse:
        profile_username = normalize_username(profile.username) if profile is not None else ""
        profile_display_name = (
            normalize_display_name(profile.display_name)
            if profile is not None
            else ""
        )
        username = profile_username or fallback_username(
            profile_display_name or user.display_name or user.id,
            uid=user.id,
        )
        effective_tier = self._daily_rewards.effective_membership_tier_user(user)
        return CommunityProfileResponse(
            uid=user.id,
            displayName=profile_display_name
            or normalize_display_name(user.display_name)
            or "XR HODL Member",
            username=username,
            avatarUrl=public_media_url(
                profile.avatar_url if profile is not None and profile.avatar_url else user.avatar_url,
                self._public_base_url,
            ),
            coverImageUrl=public_media_url(
                profile.cover_image_url if profile is not None else None,
                self._public_base_url,
            ),
            avatarUpdatedAt=(
                profile.updated_at if profile is not None and profile.avatar_url else None
            ),
            coverImageUpdatedAt=(
                profile.updated_at if profile is not None and profile.cover_image_url else None
            ),
            biography=normalize_short_text(
                profile.biography if profile is not None else "",
                max_length=160,
            ),
            birthdayLabel=normalize_short_text(
                profile.birthday_label if profile is not None else "",
                max_length=24,
            ),
            website=normalize_short_text(
                profile.website if profile is not None else "",
                max_length=80,
            ),
            socialAccounts=normalize_social_accounts(
                dict(profile.social_accounts_json or {}) if profile is not None else {}
            ),
            publicWatchlistSymbols=normalize_symbols(
                profile.public_watchlist_symbols_json if profile is not None else []
            ),
            blockedAccountIds=sorted(
                {
                    item.strip()
                    for item in (
                        list(profile.blocked_account_ids_json or [])
                        if profile is not None
                        else []
                    )
                    if str(item).strip()
                }
            ),
            usernameUpdatedAt=profile.username_updated_at if profile is not None else None,
            displayNameWindowStartedAt=(
                profile.display_name_window_started_at if profile is not None else None
            ),
            displayNameChangeCount=non_negative_int(
                profile.display_name_change_count if profile is not None else 0
            ),
            membershipTier=effective_tier,
            isPro=effective_tier != "free",
        )

    def post_response(
        self,
        post: Post,
        user: User | None,
        profile: CommunityProfile | None,
        reaction_counts: dict[int, int],
    ) -> CommunityPostResponse:
        if user is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Profile not found.")
        author_membership_tier = self._daily_rewards.effective_membership_tier_user(user)
        profile_username = normalize_username(profile.username) if profile is not None else ""
        author_name = (
            normalize_display_name(profile.display_name)
            if profile is not None
            else normalize_display_name(user.display_name)
        ) or "XR HODL Pro"
        symbols = normalize_symbols(post.symbols_json)
        symbol = normalize_symbol(post.symbol) or (symbols[0] if symbols else None)
        if symbol is not None and symbol not in symbols:
            symbols = [symbol, *symbols]
        poll = None
        poll_options = [normalize_short_text(item, max_length=60) for item in list(post.poll_options_json or [])]
        poll_options = [item for item in poll_options if item]
        if len(poll_options) >= 2 and post.poll_duration_days is not None:
            counts = [non_negative_int(item) for item in list(post.poll_vote_counts_json or [])]
            while len(counts) < len(poll_options):
                counts.append(0)
            poll = CommunityPollResponse(
                options=poll_options,
                voteCounts=counts[: len(poll_options)],
                totalVotes=non_negative_int(post.poll_vote_total),
                durationDays=non_negative_int(post.poll_duration_days),
                endsAt=post.poll_ends_at,
            )
        return CommunityPostResponse(
            id=post.id,
            authorUid=post.author_id,
            authorName=author_name,
            authorUsername=profile_username or fallback_username(author_name, uid=post.author_id),
            authorAvatarUrl=public_media_url(
                profile.avatar_url if profile is not None and profile.avatar_url else user.avatar_url,
                self._public_base_url,
            ),
            authorMembershipTier=author_membership_tier,
            authorIsPro=author_membership_tier != "free",
            content=normalize_short_text(post.content, max_length=2000),
            symbol=symbol,
            symbols=symbols,
            imageUrl=public_media_url(post.image_url, self._public_base_url),
            marketBias=normalize_market_bias(post.market_bias),
            poll=poll,
            commentCount=non_negative_int(post.comment_count),
            viewCount=non_negative_int(post.view_count),
            reactionCounts={key: value for key, value in reaction_counts.items() if value > 0},
            createdAt=post.created_at,
        )

    def comment_response(
        self,
        comment: Comment,
        user: User | None,
        profile: CommunityProfile | None,
        reaction_counts: dict[int, int],
    ) -> CommunityCommentResponse:
        if user is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Profile not found.")
        author_name = (
            normalize_display_name(profile.display_name)
            if profile is not None
            else normalize_display_name(user.display_name)
        ) or "XR HODL Member"
        username = (
            normalize_username(profile.username)
            if profile is not None
            else fallback_username(author_name, uid=user.id)
        )
        return CommunityCommentResponse(
            id=comment.id,
            postId=comment.post_id,
            authorUid=comment.author_id,
            authorName=author_name,
            authorUsername=username or fallback_username(author_name, uid=user.id),
            authorAvatarUrl=public_media_url(
                profile.avatar_url if profile is not None and profile.avatar_url else user.avatar_url,
                self._public_base_url,
            ),
            replyToCommentId=nullable_text(comment.reply_to_comment_id),
            replyToAuthorUsername=nullable_text(comment.reply_to_author_username),
            content=normalize_short_text(comment.content, max_length=1000),
            createdAt=comment.created_at,
            reactionCounts={key: value for key, value in reaction_counts.items() if value > 0},
        )

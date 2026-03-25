from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.entities import NewsArticle, NewsArticleTranslation, NewsFeedState
from app.services.news_feed_service import (
    DAILY_MAX_FETCH_CYCLES,
    DAILY_MAX_RELEASED_ARTICLES,
    DEFAULT_SOURCE_DAILY_CAP,
    INGEST_MAX_ITEMS_PER_FEED,
    INGEST_MIN_INTERVAL_SECONDS,
    MAX_STORED_ARTICLES,
    RELEASE_SOURCE_ROTATION_LOOKAHEAD,
    RETENTION_DAYS,
    SOURCE_DAILY_CAPS,
    TRANSLATE_BACKFILL_MIN_INTERVAL_SECONDS,
    StoredNewsEntry,
    _coerce_article_image_urls,
    _compose_translations,
    _first_image_url,
    _local_day_bounds,
    _site_key_for_source,
    _trim_stored_articles,
    _today_str,
    _translation_targets_for_source,
    _utc_now,
)


def _reset_state_for_new_day(state: NewsFeedState, *, now: datetime) -> None:
    today = _today_str(now)
    if state.date == today:
        return
    state.date = today
    state.daily_released_count = 0
    state.daily_fetch_count = 0
    state.last_released_at = None
    state.last_ingest_at = None
    state.last_translate_at = None


async def _get_or_create_state(db: AsyncSession, *, now: datetime) -> NewsFeedState:
    row = await db.scalar(select(NewsFeedState).where(NewsFeedState.id == 1))
    if row is not None:
        _reset_state_for_new_day(row, now=now)
        return row
    state = NewsFeedState(
        id=1,
        date=_today_str(now),
        daily_released_count=0,
        daily_fetch_count=0,
        last_released_at=None,
        last_ingest_at=None,
        last_cleanup_at=None,
        last_translate_at=None,
    )
    db.add(state)
    await db.flush()
    await db.commit()
    return state


async def maybe_cleanup_old_articles(db: AsyncSession, *, now: datetime) -> None:
    state = await _get_or_create_state(db, now=now)
    if state.last_cleanup_at is not None and (now - state.last_cleanup_at).total_seconds() < 6 * 60 * 60:
        return

    cutoff = now - timedelta(days=RETENTION_DAYS)
    await db.execute(
        delete(NewsArticle).where(func.coalesce(NewsArticle.published_at, NewsArticle.created_at) < cutoff)
    )
    await _trim_stored_articles(db, keep=MAX_STORED_ARTICLES)
    state.last_cleanup_at = now
    await db.commit()


async def maybe_ingest(
    db: AsyncSession,
    *,
    now: datetime,
    ensure_articles_ingested: Callable[..., Awaitable[int]],
) -> None:
    state = await _get_or_create_state(db, now=now)
    _reset_state_for_new_day(state, now=now)
    if state.daily_fetch_count >= DAILY_MAX_FETCH_CYCLES:
        await db.commit()
        return
    if state.last_ingest_at is not None and (now - state.last_ingest_at).total_seconds() < INGEST_MIN_INTERVAL_SECONDS:
        return

    await ensure_articles_ingested(db, max_each_feed=INGEST_MAX_ITEMS_PER_FEED)
    state.last_ingest_at = now
    state.daily_fetch_count += 1
    await db.commit()


async def _load_recent_released_articles(
    db: AsyncSession,
    *,
    limit: int = 8,
) -> list[NewsArticle]:
    stmt = (
        select(NewsArticle)
        .where(NewsArticle.released_at.is_not(None))
        .order_by(NewsArticle.released_at.desc().nullslast(), NewsArticle.id.desc())
        .limit(max(1, int(limit)))
    )
    return (await db.execute(stmt)).scalars().all()


async def _load_today_site_release_counts(
    db: AsyncSession,
    *,
    now: datetime,
) -> dict[str, int]:
    day_start, day_end = _local_day_bounds(now)
    stmt = (
        select(NewsArticle.source)
        .where(NewsArticle.released_at.is_not(None))
        .where(NewsArticle.released_at >= day_start)
        .where(NewsArticle.released_at < day_end)
    )
    sources = (await db.execute(stmt)).scalars().all()
    counts: dict[str, int] = {}
    for source in sources:
        site_key = _site_key_for_source(str(source or ""))
        counts[site_key] = counts.get(site_key, 0) + 1
    return counts


def _count_recent_regular_streak(released_articles: list[NewsArticle]) -> int:
    streak = 0
    for article in released_articles:
        if bool(article.is_liquidation):
            break
        streak += 1
    return streak


def _should_prioritize_liquidation(released_articles: list[NewsArticle]) -> bool:
    return _count_recent_regular_streak(released_articles) >= 4


def _last_released_site_key(released_articles: list[NewsArticle]) -> str | None:
    if not released_articles:
        return None
    source = str(released_articles[0].source or "").strip()
    if not source:
        return None
    return _site_key_for_source(source)


def _daily_source_cap(site_key: str) -> int:
    normalized = (site_key or "").strip().lower()
    return int(SOURCE_DAILY_CAPS.get(normalized, DEFAULT_SOURCE_DAILY_CAP))


def _candidate_published_sort_ts(candidate: NewsArticle) -> float:
    reference = candidate.published_at or candidate.created_at
    if reference is None:
        return 0.0
    return reference.timestamp()


def _pick_rotated_release_candidate(
    candidates: list[NewsArticle],
    *,
    last_site_key: str | None,
    prefer_liquidation: bool,
    site_release_counts: dict[str, int],
) -> NewsArticle | None:
    if not candidates:
        return None

    candidate_base = [
        candidate
        for candidate in candidates
        if site_release_counts.get(_site_key_for_source(str(candidate.source or "")), 0)
        < _daily_source_cap(_site_key_for_source(str(candidate.source or "")))
    ]
    if not candidate_base:
        return None

    candidate_pool = candidate_base
    if last_site_key:
        normalized_last_site_key = last_site_key.strip().lower()
        rotated_pool = [
            candidate
            for candidate in candidate_pool
            if _site_key_for_source(str(candidate.source or "")) != normalized_last_site_key
        ]
        if rotated_pool:
            candidate_pool = rotated_pool

    preferred_candidates = [
        candidate
        for candidate in candidate_pool
        if bool(candidate.is_liquidation) is prefer_liquidation
    ]
    candidate_pool = preferred_candidates or candidate_pool

    return min(
        candidate_pool,
        key=lambda candidate: (
            site_release_counts.get(_site_key_for_source(str(candidate.source or "")), 0),
            -_candidate_published_sort_ts(candidate),
        ),
    )


async def release_pending_articles(
    db: AsyncSession,
    *,
    now: datetime,
    ensure_article_translations: Callable[..., Awaitable[int]],
    build_gemini_client: Callable[..., Awaitable[object | None]],
) -> list[int]:
    state = await _get_or_create_state(db, now=now)
    if state.daily_released_count >= DAILY_MAX_RELEASED_ARTICLES:
        await db.flush()
        return []
    if state.last_released_at is not None and (
        now - state.last_released_at
    ).total_seconds() < TRANSLATE_BACKFILL_MIN_INTERVAL_SECONDS:
        return []

    cutoff = now - timedelta(days=RETENTION_DAYS)
    candidate_sort_key = func.coalesce(NewsArticle.published_at, NewsArticle.created_at)
    candidate_stmt = (
        select(NewsArticle)
        .where(NewsArticle.released_at.is_(None))
        .where(candidate_sort_key >= cutoff)
        .order_by(candidate_sort_key.desc(), NewsArticle.id.desc())
        .limit(RELEASE_SOURCE_ROTATION_LOOKAHEAD)
    )

    candidates = [
        candidate
        for candidate in (await db.execute(candidate_stmt)).scalars().all()
        if _translation_targets_for_source(str(candidate.source or ""))
    ]
    recent_released_articles = await _load_recent_released_articles(db, limit=8)
    site_release_counts = await _load_today_site_release_counts(db, now=now)
    candidate = _pick_rotated_release_candidate(
        candidates,
        last_site_key=_last_released_site_key(recent_released_articles),
        prefer_liquidation=_should_prioritize_liquidation(recent_released_articles),
        site_release_counts=site_release_counts,
    )
    if candidate is None:
        await db.flush()
        return []

    gemini = None
    gemini = await build_gemini_client(db)
    if gemini is None:
        return []
    await ensure_article_translations(db, article=candidate, gemini=gemini)

    rows = (
        await db.execute(
            select(NewsArticleTranslation).where(NewsArticleTranslation.article_id == candidate.id)
        )
    ).scalars().all()
    entry = StoredNewsEntry(
        article_id=int(candidate.id),
        uid=str(candidate.uid),
        source=str(candidate.source),
        url=str(candidate.url),
        image_url=_first_image_url(_coerce_article_image_urls(candidate)),
        image_urls=_coerce_article_image_urls(candidate),
        published_at=candidate.published_at,
        released_at=candidate.released_at,
        is_liquidation=candidate.is_liquidation,
        category=str(candidate.category or "altcoins"),
        view_count=int(candidate.view_count or 0),
        translations=_compose_translations(candidate, rows),
    )
    if not entry.is_notification_ready():
        state.last_translate_at = now
        await db.commit()
        return []

    candidate.released_at = now
    released_article_ids = [int(candidate.id)]
    state.daily_released_count += 1
    state.last_released_at = now
    state.last_translate_at = now
    await db.commit()
    return released_article_ids


async def run_news_pipeline(
    db: AsyncSession,
    *,
    now: datetime | None = None,
) -> None:
    current_now = now or _utc_now()
    await maybe_cleanup_old_articles(db, now=current_now)
    from app.services.news_feed_service import _ensure_article_translations, ensure_articles_ingested
    from app.services.gemini_service import build_gemini_client

    await maybe_ingest(
        db,
        now=current_now,
        ensure_articles_ingested=ensure_articles_ingested,
    )
    await release_pending_articles(
        db,
        now=current_now,
        ensure_article_translations=_ensure_article_translations,
        build_gemini_client=build_gemini_client,
    )

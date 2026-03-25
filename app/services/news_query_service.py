from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.entities import NewsArticle, NewsArticleTranslation
from app.services.ai_provider_config_service import get_gemini_config
from app.services.news_feed_service import (
    MAX_STORED_ARTICLES,
    RETENTION_DAYS,
    StoredNewsEntry,
    _canonicalize_url,
    _coerce_article_image_urls,
    _compose_translations,
    _ensure_article_enrichment_only,
    _first_image_url,
    _is_app_lang_scoped_source,
    _normalize_lang,
    _source_lang_for_source,
    _utc_now,
)


def _apply_news_sort(stmt, *, sort: str):
    visible_at = _visible_news_timestamp_expr()
    if sort == "trending":
        return stmt.order_by(
            NewsArticle.view_count.desc(),
            visible_at.desc().nullslast(),
            NewsArticle.id.desc(),
        )
    return stmt.order_by(visible_at.desc().nullslast(), NewsArticle.id.desc())


def _visible_news_timestamp_expr():
    return func.coalesce(
        NewsArticle.published_at,
        NewsArticle.released_at,
        NewsArticle.created_at,
    )


def _visible_news_cutoff(now: datetime | None = None) -> datetime:
    return (now or _utc_now()) - timedelta(days=RETENTION_DAYS)


def _entry_updated_at(entry: StoredNewsEntry) -> datetime | None:
    return entry.published_at or entry.released_at


def _entries_updated_at(*groups: list[StoredNewsEntry]) -> str:
    newest: datetime | None = None
    for group in groups:
        for entry in group:
            candidate = _entry_updated_at(entry)
            if candidate is None:
                continue
            if newest is None or candidate > newest:
                newest = candidate
    if newest is None:
        return datetime.fromtimestamp(0, tz=timezone.utc).isoformat()
    return newest.astimezone(timezone.utc).isoformat()


def _entry_visible_for_lang(entry: StoredNewsEntry, *, lang: str) -> bool:
    normalized_lang = _normalize_lang(lang)
    if _is_app_lang_scoped_source(entry.source):
        return (
            _source_lang_for_source(entry.source) == normalized_lang
            and entry.has_lang(normalized_lang)
        )
    return entry.has_lang(normalized_lang)


def _filter_entries_for_lang(
    entries: list[StoredNewsEntry],
    *,
    lang: str,
) -> list[StoredNewsEntry]:
    return [
        entry for entry in entries if _entry_visible_for_lang(entry, lang=lang)
    ]


async def _load_language_visible_entries(
    db: AsyncSession,
    *,
    lang: str,
    sort: str,
    is_liquidation: bool | None = None,
    category: str | None = None,
) -> list[StoredNewsEntry]:
    entries = await load_visible_news_entries(
        db,
        limit=MAX_STORED_ARTICLES,
        sort=sort,
        is_liquidation=is_liquidation,
        category=category,
    )
    return _filter_entries_for_lang(entries, lang=lang)


async def count_released_news_entries(
    db: AsyncSession,
    *,
    is_liquidation: bool | None = None,
    category: str | None = None,
) -> int:
    stmt = select(func.count(NewsArticle.id)).where(NewsArticle.released_at.is_not(None))
    if is_liquidation is not None:
        stmt = stmt.where(NewsArticle.is_liquidation.is_(is_liquidation))
    if category:
        stmt = stmt.where(NewsArticle.category == category)
    return int((await db.execute(stmt)).scalar() or 0)


async def count_visible_news_entries(
    db: AsyncSession,
    *,
    is_liquidation: bool | None = None,
    category: str | None = None,
) -> int:
    stmt = select(func.count(NewsArticle.id)).where(
        _visible_news_timestamp_expr() >= _visible_news_cutoff()
    )
    if is_liquidation is not None:
        stmt = stmt.where(NewsArticle.is_liquidation.is_(is_liquidation))
    if category:
        stmt = stmt.where(NewsArticle.category == category)
    return int((await db.execute(stmt)).scalar() or 0)


async def load_released_news_entries(
    db: AsyncSession,
    *,
    limit: int,
    offset: int = 0,
    sort: str = "latest",
    is_liquidation: bool | None = None,
    only_unnotified: bool = False,
    category: str | None = None,
) -> list[StoredNewsEntry]:
    effective_limit = max(1, int(limit))
    effective_offset = max(0, int(offset))
    stmt = select(NewsArticle).where(NewsArticle.released_at.is_not(None))
    if is_liquidation is not None:
        stmt = stmt.where(NewsArticle.is_liquidation.is_(is_liquidation))
    if only_unnotified:
        stmt = stmt.where(NewsArticle.notified_at.is_(None))
    if category:
        stmt = stmt.where(NewsArticle.category == category)
    stmt = _apply_news_sort(stmt, sort=sort).limit(effective_limit).offset(effective_offset)

    articles = (await db.execute(stmt)).scalars().all()
    if not articles:
        return []
    await _backfill_missing_article_images(db, articles=articles)

    article_ids = [int(article.id) for article in articles]
    rows = (
        await db.execute(
            select(NewsArticleTranslation).where(NewsArticleTranslation.article_id.in_(article_ids))
        )
    ).scalars().all()
    rows_by_article: dict[int, list[NewsArticleTranslation]] = {}
    for row in rows:
        rows_by_article.setdefault(int(row.article_id), []).append(row)

    entries: list[StoredNewsEntry] = []
    for article in articles:
        entries.append(
            StoredNewsEntry(
                article_id=int(article.id),
                uid=str(article.uid),
                source=str(article.source),
                url=str(article.url),
                image_url=_first_image_url(_coerce_article_image_urls(article)),
                image_urls=_coerce_article_image_urls(article),
                published_at=article.published_at,
                released_at=article.released_at,
                is_liquidation=article.is_liquidation,
                category=str(article.category or "altcoins"),
                view_count=int(article.view_count or 0),
                translations=_compose_translations(article, rows_by_article.get(int(article.id), [])),
            )
        )
    return entries


async def load_visible_news_entries(
    db: AsyncSession,
    *,
    limit: int,
    offset: int = 0,
    sort: str = "latest",
    is_liquidation: bool | None = None,
    category: str | None = None,
) -> list[StoredNewsEntry]:
    effective_limit = max(1, int(limit))
    effective_offset = max(0, int(offset))
    visible_at = _visible_news_timestamp_expr()
    stmt = select(NewsArticle).where(visible_at >= _visible_news_cutoff())
    if is_liquidation is not None:
        stmt = stmt.where(NewsArticle.is_liquidation.is_(is_liquidation))
    if category:
        stmt = stmt.where(NewsArticle.category == category)
    stmt = _apply_news_sort(stmt, sort=sort).limit(effective_limit).offset(effective_offset)

    articles = (await db.execute(stmt)).scalars().all()
    if not articles:
        return []
    await _backfill_missing_article_images(db, articles=articles)

    article_ids = [int(article.id) for article in articles]
    rows = (
        await db.execute(
            select(NewsArticleTranslation).where(NewsArticleTranslation.article_id.in_(article_ids))
        )
    ).scalars().all()
    rows_by_article: dict[int, list[NewsArticleTranslation]] = {}
    for row in rows:
        rows_by_article.setdefault(int(row.article_id), []).append(row)

    entries: list[StoredNewsEntry] = []
    for article in articles:
        entries.append(
            StoredNewsEntry(
                article_id=int(article.id),
                uid=str(article.uid),
                source=str(article.source),
                url=str(article.url),
                image_url=_first_image_url(_coerce_article_image_urls(article)),
                image_urls=_coerce_article_image_urls(article),
                published_at=article.published_at,
                released_at=article.released_at,
                is_liquidation=article.is_liquidation,
                category=str(article.category or "altcoins"),
                view_count=int(article.view_count or 0),
                translations=_compose_translations(article, rows_by_article.get(int(article.id), [])),
            )
        )
    return entries


async def _backfill_missing_article_images(
    db: AsyncSession,
    *,
    articles: list[NewsArticle],
    max_backfills: int = 3,
) -> None:
    changed = False
    attempted = 0
    for article in articles:
        if attempted >= max_backfills:
            break
        if _coerce_article_image_urls(article):
            continue
        if not str(article.url or "").strip():
            continue
        attempted += 1
        if await _ensure_article_enrichment_only(db, article=article):
            changed = True
    if changed:
        await db.commit()


async def load_pending_notification_entries(
    db: AsyncSession,
    *,
    limit: int = 1,
) -> list[StoredNewsEntry]:
    candidates = await load_released_news_entries(
        db,
        limit=max(limit * 2, 8),
        only_unnotified=True,
        sort="latest",
    )
    ready = [entry for entry in candidates if entry.is_notification_ready()]
    return ready[: max(1, int(limit))]


async def squash_pending_notification_backlog(
    db: AsyncSession,
    *,
    keep: int = 1,
    now: datetime | None = None,
) -> int:
    keep_count = max(0, int(keep))
    stale_ids = (
        await db.scalars(
            select(NewsArticle.id)
            .where(NewsArticle.released_at.is_not(None))
            .where(NewsArticle.notified_at.is_(None))
            .order_by(NewsArticle.released_at.desc().nullslast(), NewsArticle.id.desc())
            .offset(keep_count)
        )
    ).all()
    unique_ids = sorted({int(article_id) for article_id in stale_ids if article_id})
    if not unique_ids:
        return 0

    marked_at = now or _utc_now()
    result = await db.execute(
        update(NewsArticle)
        .where(NewsArticle.id.in_(unique_ids))
        .values(notified_at=marked_at)
    )
    await db.commit()
    return int(result.rowcount or 0)


async def mark_news_entries_notified(
    db: AsyncSession,
    *,
    article_ids: list[int],
    now: datetime | None = None,
) -> int:
    unique_ids = sorted({int(article_id) for article_id in article_ids if article_id})
    if not unique_ids:
        return 0
    marked_at = now or _utc_now()
    result = await db.execute(
        update(NewsArticle)
        .where(NewsArticle.id.in_(unique_ids))
        .values(notified_at=marked_at)
    )
    await db.commit()
    return int(result.rowcount or 0)


async def record_news_view(
    db: AsyncSession,
    *,
    article_id: int | None = None,
    url: str | None = None,
) -> int | None:
    article: NewsArticle | None = None
    if article_id is not None:
        article = await db.get(NewsArticle, int(article_id))
    elif url:
        raw_url = str(url or "").strip()
        normalized_url = _canonicalize_url(raw_url)
        article = await db.scalar(select(NewsArticle).where(NewsArticle.url == raw_url).limit(1))
        if article is None:
            article = await db.scalar(select(NewsArticle).where(NewsArticle.url == normalized_url).limit(1))
        if article is None:
            candidates = (
                await db.execute(
                    select(NewsArticle)
                    .where(_visible_news_timestamp_expr() >= _visible_news_cutoff())
                    .order_by(_visible_news_timestamp_expr().desc().nullslast(), NewsArticle.id.desc())
                    .limit(200)
                )
            ).scalars().all()
            for candidate in candidates:
                if _canonicalize_url(candidate.url) == normalized_url:
                    article = candidate
                    break
    if article is None:
        return None

    article.view_count = int(article.view_count or 0) + 1
    await db.commit()
    return int(article.view_count)


async def build_news_cache_revision(
    db: AsyncSession,
    *,
    is_liquidation: bool | None = None,
    category: str | None = None,
    include_views: bool = True,
    lang: str | None = None,
) -> str:
    if lang is not None:
        entries = await _load_language_visible_entries(
            db,
            lang=lang,
            sort="trending" if include_views else "latest",
            is_liquidation=is_liquidation,
            category=category,
        )
        newest = _entries_updated_at(entries)
        total_views = (
            sum(int(entry.view_count or 0) for entry in entries)
            if include_views
            else 0
        )
        max_article_id = max((entry.article_id for entry in entries), default=0)
        visible_ts = (
            int(datetime.fromisoformat(newest).astimezone(timezone.utc).timestamp())
            if entries
            else 0
        )
        return ":".join(
            (
                str(len(entries)),
                str(int(max_article_id or 0)),
                str(visible_ts),
                str(total_views),
            )
        )

    visible_at = _visible_news_timestamp_expr()
    stmt = select(
        func.count(NewsArticle.id),
        func.max(visible_at),
        func.max(NewsArticle.id),
    ).where(visible_at >= _visible_news_cutoff())
    if include_views:
        stmt = stmt.add_columns(func.coalesce(func.sum(NewsArticle.view_count), 0))
    if is_liquidation is not None:
        stmt = stmt.where(NewsArticle.is_liquidation.is_(is_liquidation))
    if category:
        stmt = stmt.where(NewsArticle.category == category)

    row = (await db.execute(stmt)).one()
    count, latest_visible_at, max_article_id, *rest = row
    total_views = int(rest[0] or 0) if rest else 0
    visible_ts = (
        int(latest_visible_at.astimezone(timezone.utc).timestamp())
        if isinstance(latest_visible_at, datetime)
        else 0
    )
    return ":".join(
        (
            str(int(count or 0)),
            str(int(max_article_id or 0)),
            str(visible_ts),
            str(total_views),
        )
    )


async def build_news_list_payload(
    db: AsyncSession,
    *,
    lang: str,
    page: int,
    page_size: int,
    sort: str,
    category: str | None,
) -> dict[str, object]:
    normalized_lang = _normalize_lang(lang)
    normalized_sort = "trending" if (sort or "").strip().lower() == "trending" else "latest"
    normalized_page = max(1, int(page or 1))
    normalized_page_size = max(1, min(int(page_size or 20), 30))
    normalized_category = (category or "").strip().lower() or None
    offset = (normalized_page - 1) * normalized_page_size

    visible_entries = await _load_language_visible_entries(
        db,
        lang=normalized_lang,
        sort=normalized_sort,
        is_liquidation=False,
        category=normalized_category,
    )
    total = len(visible_entries)
    entries = visible_entries[offset : offset + normalized_page_size]
    items = [entry.to_payload_item(lang=normalized_lang) for entry in entries]
    total_pages = max(1, (total + normalized_page_size - 1) // normalized_page_size) if total else 1
    gemini_cfg = await get_gemini_config(db) if normalized_lang != "en" else None
    ai_enabled = normalized_lang == "en" or bool(gemini_cfg)

    return {
        "items": items,
        "page": normalized_page,
        "pageSize": normalized_page_size,
        "total": total,
        "totalPages": total_pages,
        "hasMore": offset + len(items) < total,
        "lang": normalized_lang,
        "sort": normalized_sort,
        "category": normalized_category or "all",
        "aiEnabled": ai_enabled,
        "updatedAt": _entries_updated_at(entries),
    }


async def build_news_feed_payload(
    db: AsyncSession,
    *,
    lang: str,
    limit: int,
) -> dict[str, object]:
    normalized_lang = _normalize_lang(lang)
    effective_limit = max(1, min(int(limit or 12), 20))

    latest_entries = (
        await _load_language_visible_entries(
            db,
            lang=normalized_lang,
            sort="latest",
            is_liquidation=False,
        )
    )[:effective_limit]
    liquidation_entries = (
        await _load_language_visible_entries(
            db,
            lang=normalized_lang,
            sort="latest",
            is_liquidation=True,
        )
    )[:effective_limit]

    latest = [entry.to_payload_item(lang=normalized_lang) for entry in latest_entries]
    liquidations = [entry.to_payload_item(lang=normalized_lang) for entry in liquidation_entries]
    gemini_cfg = await get_gemini_config(db) if normalized_lang != "en" else None
    ai_enabled = normalized_lang == "en" or bool(gemini_cfg)

    return {
        "latest": latest,
        "liquidations": liquidations,
        "updatedAt": _entries_updated_at(latest_entries, liquidation_entries),
        "lang": normalized_lang,
        "aiEnabled": ai_enabled,
        "limit": effective_limit,
    }

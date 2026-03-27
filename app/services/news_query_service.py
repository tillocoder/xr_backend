from __future__ import annotations

from datetime import datetime, timedelta, timezone
import re
from urllib.parse import urlparse

from sqlalchemy import and_, exists, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.entities import NewsArticle, NewsArticleTranslation
from app.services.ai_provider_config_service import get_gemini_config
from app.services.news_feed_service import (
    MAX_STORED_ARTICLES,
    RSS_FEEDS,
    RETENTION_DAYS,
    StoredNewsEntry,
    _canonicalize_url,
    _coerce_article_image_urls,
    _compose_translations,
    _first_image_url,
    _is_app_lang_scoped_source,
    _normalize_lang,
    _shared_numeric_tokens,
    _token_overlap_score,
    _source_lang_for_source,
    _utc_now,
)


_RELATED_GENERIC_TOKENS = frozenset(
    {
        "about",
        "and",
        "are",
        "after",
        "ahead",
        "amid",
        "as",
        "at",
        "analysis",
        "asset",
        "assets",
        "be",
        "bet",
        "bets",
        "bitcoin",
        "blockchain",
        "breakout",
        "bullish",
        "coin",
        "coins",
        "crypto",
        "cryptocurrency",
        "cryptocurrencies",
        "demand",
        "drop",
        "drops",
        "edge",
        "ecosystem",
        "etf",
        "exchange",
        "exchanges",
        "falls",
        "fear",
        "forecast",
        "gain",
        "gains",
        "golden",
        "greed",
        "growth",
        "improve",
        "improves",
        "investor",
        "investors",
        "key",
        "liquidation",
        "liquidations",
        "macro",
        "market",
        "markets",
        "news",
        "outlook",
        "post",
        "prediction",
        "predictions",
        "price",
        "prices",
        "react",
        "reaction",
        "rally",
        "report",
        "reports",
        "risk",
        "risks",
        "rise",
        "rises",
        "sec",
        "sentiment",
        "shows",
        "signal",
        "signals",
        "stock",
        "stocks",
        "surge",
        "surges",
        "target",
        "targets",
        "traction",
        "token",
        "tokens",
        "trader",
        "traders",
        "trading",
        "trend",
        "trends",
        "update",
        "updates",
        "validator",
        "validators",
        "the",
        "than",
        "then",
        "that",
        "this",
        "these",
        "those",
        "with",
        "from",
        "into",
        "through",
        "while",
        "when",
        "where",
        "what",
        "which",
        "for",
        "has",
        "not",
        "most",
        "some",
        "still",
        "between",
        "down",
        "support",
        "will",
        "would",
        "could",
        "should",
        "their",
        "there",
        "them",
        "they",
        "its",
        "here",
        "long",
        "short",
        "ratio",
        "ratios",
        "position",
        "positions",
        "positioning",
        "derivative",
        "derivatives",
        "extreme",
        "unusual",
        "latest",
        "today",
        "tomorrow",
        "whale",
        "whales",
        "watch",
        "watches",
        "yangilik",
        "yangiliklar",
        "maqola",
        "maqolasi",
        "maqolani",
        "narx",
        "narxi",
        "bozor",
        "bozori",
        "bozorlar",
        "kripto",
        "kriptovalyuta",
        "kriptovalyutalar",
        "новости",
        "новость",
        "рынок",
        "рынка",
        "рынки",
        "цена",
        "цены",
        "крипто",
        "криптовалюта",
        "криптовалюты",
        "статья",
        "статью",
    }
)
_RELATED_ALIAS_GROUPS: dict[str, frozenset[str]] = {
    "bitcoin": frozenset({"bitcoin", "btc"}),
    "ethereum": frozenset({"ethereum", "eth", "ether"}),
    "solana": frozenset({"solana", "sol"}),
    "bittensor": frozenset({"bittensor", "tao"}),
    "ripple": frozenset({"ripple", "xrp"}),
    "binance": frozenset({"binance", "bnb"}),
    "ton": frozenset({"ton", "toncoin"}),
    "tron": frozenset({"tron", "trx"}),
    "cardano": frozenset({"cardano", "ada"}),
    "dogecoin": frozenset({"dogecoin", "doge"}),
    "federal_reserve": frozenset({"fed", "federal", "reserve"}),
}
_RELATED_ALIAS_LOOKUP: dict[str, str] = {
    alias: canonical
    for canonical, aliases in _RELATED_ALIAS_GROUPS.items()
    for alias in aliases
}


_APP_LANG_SCOPED_SOURCES: tuple[str, ...] = tuple(
    provider.source for provider in RSS_FEEDS if provider.app_lang_scoped
)
_APP_LANG_SCOPED_SOURCES_BY_LANG: dict[str, tuple[str, ...]] = {
    lang: tuple(
        provider.source
        for provider in RSS_FEEDS
        if provider.app_lang_scoped
        and _normalize_lang(provider.source_lang) == lang
    )
    for lang in ("en", "ru", "uz")
}


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


def _has_text_expr(*columns):
    clauses = [
        func.length(func.btrim(func.coalesce(column, ""))) > 0 for column in columns
    ]
    return or_(*clauses)


def _translation_exists_expr(lang: str):
    normalized_lang = _normalize_lang(lang)
    return exists(
        select(1)
        .select_from(NewsArticleTranslation)
        .where(NewsArticleTranslation.article_id == NewsArticle.id)
        .where(NewsArticleTranslation.lang == normalized_lang)
        .where(
            _has_text_expr(
                NewsArticleTranslation.title,
                NewsArticleTranslation.summary,
            )
        )
    )


def _language_visibility_expr(lang: str):
    normalized_lang = _normalize_lang(lang)
    article_has_text = _has_text_expr(
        NewsArticle.raw_title,
        NewsArticle.raw_summary,
    )
    scoped_sources = _APP_LANG_SCOPED_SOURCES_BY_LANG.get(normalized_lang, ())
    branches = []

    if scoped_sources:
        branches.append(
            and_(
                NewsArticle.source.in_(scoped_sources),
                article_has_text,
            )
        )

    non_scoped_branch = (
        ~NewsArticle.source.in_(_APP_LANG_SCOPED_SOURCES)
        if _APP_LANG_SCOPED_SOURCES
        else article_has_text
    )
    if normalized_lang == "en":
        branches.append(and_(non_scoped_branch, article_has_text))
    else:
        branches.append(and_(non_scoped_branch, _translation_exists_expr(normalized_lang)))

    return or_(*branches)


async def _load_language_visible_entries(
    db: AsyncSession,
    *,
    lang: str,
    sort: str,
    is_liquidation: bool | None = None,
    category: str | None = None,
) -> list[StoredNewsEntry]:
    return await _load_language_visible_entries_window(
        db,
        lang=lang,
        sort=sort,
        limit=MAX_STORED_ARTICLES,
        offset=0,
        is_liquidation=is_liquidation,
        category=category,
    )


async def _load_language_visible_entries_window(
    db: AsyncSession,
    *,
    lang: str,
    sort: str,
    limit: int,
    offset: int = 0,
    is_liquidation: bool | None = None,
    category: str | None = None,
) -> list[StoredNewsEntry]:
    effective_limit = max(1, int(limit))
    effective_offset = max(0, int(offset))
    visible_at = _visible_news_timestamp_expr()
    stmt = (
        select(NewsArticle)
        .where(visible_at >= _visible_news_cutoff())
        .where(_language_visibility_expr(lang))
    )
    if is_liquidation is not None:
        stmt = stmt.where(NewsArticle.is_liquidation.is_(is_liquidation))
    if category:
        stmt = stmt.where(NewsArticle.category == category)
    stmt = _apply_news_sort(stmt, sort=sort).limit(effective_limit).offset(
        effective_offset
    )
    articles = (await db.execute(stmt)).scalars().all()
    return await _entries_from_articles(db, articles=articles)


async def _count_language_visible_entries(
    db: AsyncSession,
    *,
    lang: str,
    is_liquidation: bool | None = None,
    category: str | None = None,
) -> int:
    visible_at = _visible_news_timestamp_expr()
    stmt = select(func.count(NewsArticle.id)).where(
        visible_at >= _visible_news_cutoff()
    ).where(_language_visibility_expr(lang))
    if is_liquidation is not None:
        stmt = stmt.where(NewsArticle.is_liquidation.is_(is_liquidation))
    if category:
        stmt = stmt.where(NewsArticle.category == category)
    return int((await db.execute(stmt)).scalar() or 0)


async def _build_language_revision(
    db: AsyncSession,
    *,
    lang: str,
    is_liquidation: bool | None = None,
    category: str | None = None,
    include_views: bool = True,
) -> str:
    visible_at = _visible_news_timestamp_expr()
    stmt = select(
        func.count(NewsArticle.id),
        func.max(visible_at),
        func.max(NewsArticle.id),
    ).where(visible_at >= _visible_news_cutoff()).where(
        _language_visibility_expr(lang)
    )
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


async def _build_news_slice_payload(
    db: AsyncSession,
    *,
    lang: str,
    sort: str,
    limit: int,
    offset: int = 0,
    is_liquidation: bool | None = None,
    category: str | None = None,
) -> list[StoredNewsEntry]:
    return await _load_language_visible_entries_window(
        db,
        lang=lang,
        sort=sort,
        limit=limit,
        offset=offset,
        is_liquidation=is_liquidation,
        category=category,
    )


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
    return await _entries_from_articles(db, articles=articles)


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
    return await _entries_from_articles(db, articles=articles)


async def _entries_from_articles(
    db: AsyncSession,
    *,
    articles: list[NewsArticle],
) -> list[StoredNewsEntry]:
    if not articles:
        return []

    article_ids = [int(article.id) for article in articles]
    rows = (
        await db.execute(
            select(NewsArticleTranslation).where(NewsArticleTranslation.article_id.in_(article_ids))
        )
    ).scalars().all()
    rows_by_article: dict[int, list[NewsArticleTranslation]] = {}
    for row in rows:
        rows_by_article.setdefault(int(row.article_id), []).append(row)

    return [
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
            translations=_compose_translations(
                article,
                rows_by_article.get(int(article.id), []),
            ),
        )
        for article in articles
    ]


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
        return await _build_language_revision(
            db,
            lang=_normalize_lang(lang),
            include_views=include_views,
            is_liquidation=is_liquidation,
            category=category,
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

    total = await _count_language_visible_entries(
        db,
        lang=normalized_lang,
        is_liquidation=False,
        category=normalized_category,
    )
    entries = await _load_language_visible_entries_window(
        db,
        lang=normalized_lang,
        sort=normalized_sort,
        limit=normalized_page_size,
        offset=offset,
        is_liquidation=False,
        category=normalized_category,
    )
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

    latest_entries = await _load_language_visible_entries_window(
        db,
        lang=normalized_lang,
        sort="latest",
        limit=effective_limit,
        offset=0,
        is_liquidation=False,
    )
    liquidation_entries = await _load_language_visible_entries_window(
        db,
        lang=normalized_lang,
        sort="latest",
        limit=effective_limit,
        offset=0,
        is_liquidation=True,
    )

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


async def build_related_news_payload(
    db: AsyncSession,
    *,
    lang: str,
    url: str,
    limit: int,
) -> dict[str, object]:
    normalized_lang = _normalize_lang(lang)
    effective_limit = max(1, min(int(limit or 18), 36))
    anchor_article = await _find_news_article_by_url(db, url=url)
    if anchor_article is None:
        gemini_cfg = await get_gemini_config(db) if normalized_lang != "en" else None
        ai_enabled = normalized_lang == "en" or bool(gemini_cfg)
        return {
            "items": [],
            "updatedAt": datetime.fromtimestamp(0, tz=timezone.utc).isoformat(),
            "lang": normalized_lang,
            "aiEnabled": ai_enabled,
            "limit": effective_limit,
            "category": "all",
        }

    anchor_entry_rows = await _entries_from_articles(db, articles=[anchor_article])
    if not anchor_entry_rows:
        gemini_cfg = await get_gemini_config(db) if normalized_lang != "en" else None
        ai_enabled = normalized_lang == "en" or bool(gemini_cfg)
        return {
            "items": [],
            "updatedAt": datetime.fromtimestamp(0, tz=timezone.utc).isoformat(),
            "lang": normalized_lang,
            "aiEnabled": ai_enabled,
            "limit": effective_limit,
            "category": str(anchor_article.category or "all"),
        }

    anchor_entry = anchor_entry_rows[0]
    candidate_articles = await _load_related_news_candidates(
        db,
        lang=normalized_lang,
        anchor_article=anchor_article,
        limit=max(72, effective_limit * 10),
    )
    candidate_entries = await _entries_from_articles(db, articles=candidate_articles)
    ranked_entries = _rank_related_news_entries(
        anchor_entry,
        candidate_entries,
        lang=normalized_lang,
    )
    top_entries = ranked_entries[:effective_limit]
    gemini_cfg = await get_gemini_config(db) if normalized_lang != "en" else None
    ai_enabled = normalized_lang == "en" or bool(gemini_cfg)

    return {
        "items": [entry.to_payload_item(lang=normalized_lang) for entry in top_entries],
        "updatedAt": _entries_updated_at(top_entries),
        "lang": normalized_lang,
        "aiEnabled": ai_enabled,
        "limit": effective_limit,
        "category": anchor_entry.category,
    }


async def _find_news_article_by_url(
    db: AsyncSession,
    *,
    url: str,
) -> NewsArticle | None:
    raw_url = str(url or "").strip()
    if not raw_url:
        return None
    normalized_url = _canonicalize_url(raw_url)

    article = await db.scalar(
        select(NewsArticle).where(NewsArticle.url == raw_url).limit(1)
    )
    if article is not None:
        return article
    if normalized_url and normalized_url != raw_url:
        article = await db.scalar(
            select(NewsArticle).where(NewsArticle.url == normalized_url).limit(1)
        )
        if article is not None:
            return article

    visible_candidates = (
        await db.execute(
            select(NewsArticle)
            .where(_visible_news_timestamp_expr() >= _visible_news_cutoff())
            .order_by(_visible_news_timestamp_expr().desc().nullslast(), NewsArticle.id.desc())
            .limit(200)
        )
    ).scalars().all()
    for candidate in visible_candidates:
        if _canonicalize_url(str(candidate.url or "")) == normalized_url:
            return candidate
    return None


async def _load_related_news_candidates(
    db: AsyncSession,
    *,
    lang: str,
    anchor_article: NewsArticle,
    limit: int,
) -> list[NewsArticle]:
    effective_limit = max(24, int(limit))
    broad_limit = max(effective_limit, min(160, effective_limit * 2))
    visible_at = _visible_news_timestamp_expr()
    base_stmt = (
        select(NewsArticle)
        .where(visible_at >= _visible_news_cutoff())
        .where(_language_visibility_expr(lang))
        .where(NewsArticle.id != anchor_article.id)
    )

    scoped_stmt = base_stmt.where(
        NewsArticle.category == anchor_article.category
    )
    scoped_stmt = _apply_news_sort(scoped_stmt, sort="latest").limit(effective_limit)
    scoped_articles = (await db.execute(scoped_stmt)).scalars().all()

    fallback_stmt = _apply_news_sort(base_stmt, sort="latest").limit(broad_limit)
    fallback_articles = (await db.execute(fallback_stmt)).scalars().all()
    if not scoped_articles:
        return fallback_articles

    seen_ids = {int(article.id) for article in scoped_articles}
    merged = list(scoped_articles)
    for article in fallback_articles:
        article_id = int(article.id)
        if article_id in seen_ids:
            continue
        seen_ids.add(article_id)
        merged.append(article)
        if len(merged) >= effective_limit:
            break
    return merged


def _rank_related_news_entries(
    anchor_entry: StoredNewsEntry,
    candidates: list[StoredNewsEntry],
    *,
    lang: str,
) -> list[StoredNewsEntry]:
    normalized_lang = _normalize_lang(lang)
    anchor_payload = anchor_entry.to_payload_item(lang=normalized_lang)
    anchor_title = str(anchor_payload.get("title") or "").strip()
    anchor_summary = str(anchor_payload.get("summary") or "").strip()
    anchor_text = (
        f"{anchor_title} "
        f"{anchor_summary}"
    ).strip()
    anchor_category = str(anchor_entry.category or "").strip().lower()
    anchor_subjects = _related_subject_tokens(
        anchor_entry,
        title=anchor_title,
        summary=anchor_summary,
    )
    anchor_primary_entities = _related_primary_entity_tokens(
        anchor_entry,
        title=anchor_title,
    )
    anchor_entities = _related_entity_tokens(
        anchor_entry,
        title=anchor_title,
        summary=anchor_summary,
    )
    anchor_focus = _related_focus_tokens(anchor_entry, anchor_text)
    anchor_has_primary_entities = bool(anchor_primary_entities)
    anchor_has_entities = bool(anchor_entities)
    anchor_has_subjects = bool(anchor_subjects)
    anchor_has_specific_focus = bool(anchor_focus)

    scored: list[tuple[float, float, StoredNewsEntry]] = []
    for entry in candidates:
        payload = entry.to_payload_item(lang=normalized_lang)
        candidate_title = str(payload.get("title") or "").strip()
        candidate_summary = str(payload.get("summary") or "").strip()
        candidate_text = (
            f"{candidate_title} "
            f"{candidate_summary}"
        ).strip()
        if not candidate_text:
            continue

        candidate_subjects = _related_subject_tokens(
            entry,
            title=candidate_title,
            summary=candidate_summary,
        )
        candidate_primary_entities = _related_primary_entity_tokens(
            entry,
            title=candidate_title,
        )
        candidate_entities = _related_entity_tokens(
            entry,
            title=candidate_title,
            summary=candidate_summary,
        )
        shared_primary_entities = anchor_primary_entities & candidate_primary_entities
        shared_entities = anchor_entities & candidate_entities
        shared_subjects = anchor_subjects & candidate_subjects
        candidate_focus = _related_focus_tokens(entry, candidate_text)
        shared_focus = anchor_focus & candidate_focus
        overlap = _token_overlap_score(anchor_text, candidate_text)
        shared_numbers = _shared_numeric_tokens(anchor_text, candidate_text)
        numeric_overlap = 0.12 if shared_numbers else 0.0
        primary_entity_bonus = min(4.8, len(shared_primary_entities) * 2.8)
        entity_bonus = min(4.2, len(shared_entities) * 2.4)
        subject_bonus = min(3.4, len(shared_subjects) * 1.8)
        focus_bonus = min(1.25, len(shared_focus) * 0.45)
        if anchor_has_primary_entities:
            if not shared_primary_entities:
                if not shared_entities or not shared_subjects:
                    continue
            if len(anchor_primary_entities) > 1 and not shared_primary_entities:
                continue
        elif anchor_has_entities:
            if not shared_entities:
                continue
            if len(anchor_entities) > 1 and not shared_subjects and overlap < 0.08:
                continue
        elif anchor_has_subjects:
            if not shared_subjects:
                continue
        elif anchor_has_specific_focus:
            if not shared_focus and not shared_numbers:
                continue
        elif overlap < 0.12 and not shared_numbers:
            continue

        same_category = (
            0.9
            if anchor_category
            and entry.category.strip().lower() == anchor_category
            and anchor_category in {"btc", "eth", "macro", "liquidation"}
            else 0.18
            if anchor_category
            and entry.category.strip().lower() == anchor_category
            else 0.0
        )
        liquidation_alignment = (
            0.2 if bool(entry.is_liquidation) == bool(anchor_entry.is_liquidation) else 0.0
        )
        recency = _related_news_recency_score(entry)
        score = (
            overlap * 4.2
            + numeric_overlap
            + primary_entity_bonus
            + entity_bonus
            + subject_bonus
            + focus_bonus
            + same_category
            + liquidation_alignment
            + recency
        )
        if score < 1.05:
            continue
        scored.append((score, _entry_sort_ts(entry), entry))

    scored.sort(key=lambda row: (row[0], row[1]), reverse=True)
    return [entry for _, _, entry in scored]


def _entry_sort_ts(entry: StoredNewsEntry) -> float:
    candidate = entry.published_at or entry.released_at
    if candidate is None:
        return 0.0
    return candidate.astimezone(timezone.utc).timestamp()


def _related_news_recency_score(entry: StoredNewsEntry) -> float:
    timestamp = _entry_sort_ts(entry)
    if timestamp <= 0:
        return 0.0
    age_hours = max(0.0, (_utc_now().timestamp() - timestamp) / 3600.0)
    return max(0.0, 0.8 - min(age_hours, 72.0) / 120.0)


def _related_focus_tokens(entry: StoredNewsEntry, text: str) -> set[str]:
    tokens = _related_text_tokens(text)
    tokens.update(_related_url_tokens(entry.url))
    return _filter_related_focus_tokens(entry, tokens)


def _related_text_tokens(value: str) -> set[str]:
    raw_tokens = re.findall(r"[\w$%]+", (value or "").lower(), flags=re.UNICODE)
    normalized_tokens: set[str] = set()
    for token in raw_tokens:
        cleaned = token.strip("_")
        if len(cleaned) < 3:
            continue
        if cleaned.isdigit():
            continue
        normalized_tokens.add(cleaned)
    return normalized_tokens


def _related_url_tokens(url: str) -> set[str]:
    canonical = _canonicalize_url(url)
    if not canonical:
        return set()
    parsed = urlparse(canonical)
    segments = [
        segment.strip().lower()
        for segment in parsed.path.split("/")
        if segment.strip()
    ]
    if not segments:
        return set()
    slug = segments[-1]
    parts = {
        part
        for part in slug.replace("-", " ").replace("_", " ").split()
        if len(part) >= 3
    }
    return parts


def _expand_related_aliases(tokens: set[str]) -> set[str]:
    expanded = set(tokens)
    for token in list(tokens):
        canonical = _RELATED_ALIAS_LOOKUP.get(token)
        if canonical:
            expanded.add(canonical)
    return expanded


def _related_entity_tokens(
    entry: StoredNewsEntry,
    *,
    title: str,
    summary: str,
) -> set[str]:
    tokens = _related_text_tokens(title)
    tokens.update(_related_text_tokens(summary))
    tokens.update(_related_url_tokens(entry.url))
    return {
        canonical
        for canonical, aliases in _RELATED_ALIAS_GROUPS.items()
        if tokens & aliases
    }


def _related_primary_entity_tokens(
    entry: StoredNewsEntry,
    *,
    title: str,
) -> set[str]:
    tokens = _related_text_tokens(title)
    tokens.update(_related_url_tokens(entry.url))
    return {
        canonical
        for canonical, aliases in _RELATED_ALIAS_GROUPS.items()
        if tokens & aliases
    }


def _related_subject_tokens(
    entry: StoredNewsEntry,
    *,
    title: str,
    summary: str,
) -> set[str]:
    title_tokens = _filter_related_focus_tokens(
        entry,
        _related_text_tokens(title) | _related_url_tokens(entry.url),
    )
    if title_tokens:
        return title_tokens

    summary_tokens = _filter_related_focus_tokens(
        entry,
        _related_text_tokens(summary),
    )
    category = str(entry.category or "").strip().lower()
    if category in {"btc", "eth"}:
        return summary_tokens | {category}
    return summary_tokens


def _filter_related_focus_tokens(
    entry: StoredNewsEntry,
    tokens: set[str],
) -> set[str]:
    category = str(entry.category or "").strip().lower()
    filtered = {
        token
        for token in tokens
        if len(token) >= 3
        and token not in _RELATED_GENERIC_TOKENS
        and token != category
        and not token.isdigit()
    }
    if entry.is_liquidation:
        filtered.discard("long")
        filtered.discard("short")
    return _expand_related_aliases(filtered)

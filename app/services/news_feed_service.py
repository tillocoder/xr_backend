from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from time import mktime

import feedparser
import httpx
from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.entities import NewsArticle, NewsArticleTranslation, NewsFeedState
from app.services.ai_provider_config_service import get_gemini_config
from app.services.gemini_service import GeminiClient


RSS_FEEDS: list[tuple[str, str]] = [
    ("The Daily Hodl", "https://dailyhodl.com/feed/"),
    ("Cointelegraph", "https://cointelegraph.com/rss"),
    ("CoinDesk", "https://www.coindesk.com/arc/outboundfeeds/rss/"),
    ("BeInCrypto", "https://uz.beincrypto.com/feed/"),
    ("Kun.uz", "https://kun.uz/news/rss"),
]

TOPIC_KEYWORDS = [
    "bitcoin",
    "btc",
    "ethereum",
    "eth",
    "solana",
    "sol",
    "xrp",
    "etf",
    "spot etf",
    "bnb",
    "ton",
    "toncoin",
    "doge",
    "ada",
    "trx",
    "tron",
]

LIQ_KEYWORDS = [
    "liquidation",
    "liquidations",
    "liquidated",
    "liquidate",
    "rekt",
    "wipeout",
    "wiped out",
    "long squeeze",
    "short squeeze",
    "margin call",
    "leverage",
    "leveraged",
]

INGEST_MIN_INTERVAL_SECONDS = 10 * 60
RELEASE_MIN_INTERVAL_SECONDS = 60 * 60
DAILY_MAX_RELEASED = 12
RETENTION_DAYS = 10
TRANSLATE_BACKFILL_MIN_INTERVAL_SECONDS = 15 * 60


@dataclass(frozen=True)
class FeedItem:
    source: str
    title: str
    summary: str
    url: str
    image_url: str
    published_at: datetime | None
    is_liquidation: bool


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)

def _today_str(now: datetime | None = None) -> str:
    n = now or _utc_now()
    return n.date().isoformat()


def _uid(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:32]


def _strip_html(s: str) -> str:
    if not s:
        return ""
    s = re.sub(r"<[^>]+>", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _looks_english(text: str) -> bool:
    t = (text or "").lower()
    score = sum(w in t for w in [" the ", " and ", " to ", " in ", " of ", " is ", " are "])
    return score >= 3


def _normalize_lang(lang: str) -> str:
    base = (lang or "").strip().lower()
    if not base:
        return "en"
    base = base.split("-", 1)[0]
    if base in {"en", "uz", "ru"}:
        return base
    return "en"


def _matches_keywords(text: str, keywords: list[str]) -> bool:
    t = (text or "").lower()
    return any(k in t for k in keywords)


def _pick_image(entry) -> str:
    candidates: list[str] = []
    for enc in getattr(entry, "enclosures", []) or []:
        url = (enc.get("href") or enc.get("url") or "").strip()
        if url:
            candidates.append(url)
    for m in getattr(entry, "media_content", []) or []:
        url = (m.get("url") or "").strip()
        if url:
            candidates.append(url)
    for m in getattr(entry, "media_thumbnail", []) or []:
        url = (m.get("url") or "").strip()
        if url:
            candidates.append(url)

    content = getattr(entry, "content", None) or []
    if isinstance(content, list):
        for c in content:
            if not isinstance(c, dict):
                continue
            html = (c.get("value") or "").strip()
            url = _extract_first_image_from_html(html)
            if url:
                candidates.append(url)

    summary_raw = (getattr(entry, "summary", "") or "").strip()
    url = _extract_first_image_from_html(summary_raw)
    if url:
        candidates.append(url)

    return candidates[0] if candidates else ""

def _extract_first_image_from_html(html: str) -> str:
    if not html:
        return ""
    m = re.search(r'<img[^>]+src=["\']([^"\']+)["\']', html, flags=re.I)
    if m:
        return (m.group(1) or "").strip()
    m = re.search(r'srcset=["\']([^"\']+)["\']', html, flags=re.I)
    if m:
        raw = (m.group(1) or "").strip()
        first = raw.split(",", 1)[0].strip()
        return first.split(" ", 1)[0].strip()
    return ""


def _entry_published_at(entry) -> datetime | None:
    st = getattr(entry, "published_parsed", None) or getattr(entry, "updated_parsed", None)
    if not st:
        return None
    try:
        return datetime.fromtimestamp(mktime(st), tz=timezone.utc)
    except Exception:
        return None


async def _fetch_feed_xml(url: str) -> str:
    timeout = httpx.Timeout(connect=8, read=16, write=10, pool=10)
    limits = httpx.Limits(max_connections=8, max_keepalive_connections=4)
    async with httpx.AsyncClient(
        timeout=timeout,
        limits=limits,
        headers={"User-Agent": "XRHodlBackend/1.0", "Accept": "application/rss+xml, application/xml, text/xml, */*"},
        follow_redirects=True,
    ) as client:
        r = await client.get(url)
        if r.status_code >= 400:
            return ""
        return (r.text or "").strip()


async def fetch_feed_items(*, max_each: int = 30) -> list[FeedItem]:
    results: list[FeedItem] = []

    async def fetch_one(source: str, url: str) -> None:
        try:
            xml = await _fetch_feed_xml(url)
        except Exception:
            return
        if not xml:
            return
        parsed = feedparser.parse(xml)
        for e in (parsed.entries or [])[:max_each]:
            title = (getattr(e, "title", "") or "").strip()
            link = (getattr(e, "link", "") or "").strip()
            if not title or not link:
                continue
            summary_raw = (getattr(e, "summary", "") or "").strip()
            summary = _strip_html(summary_raw)
            text = f"{title} {summary}".strip()
            is_liq = _matches_keywords(text, LIQ_KEYWORDS)

            # Keep only relevant stories for the app feed.
            is_relevant = is_liq or _matches_keywords(text, TOPIC_KEYWORDS) or source.lower() in {
                "beincrypto",
                "kun.uz",
            }
            if not is_relevant:
                continue

            results.append(
                FeedItem(
                    source=source,
                    title=title,
                    summary=summary,
                    url=link,
                    image_url=_pick_image(e),
                    published_at=_entry_published_at(e),
                    is_liquidation=is_liq,
                )
            )

    tasks = [fetch_one(source, url) for source, url in RSS_FEEDS]
    await _gather_safely(tasks)
    results.sort(key=lambda x: x.published_at or datetime.fromtimestamp(0, tz=timezone.utc), reverse=True)
    return results


async def _gather_safely(tasks):
    import asyncio

    out = await asyncio.gather(*tasks, return_exceptions=True)
    return out


def _extract_json_object(text: str) -> dict | None:
    raw = (text or "").strip()
    if not raw:
        return None
    raw = raw.replace("```json", "```").replace("```JSON", "```")
    raw = raw.strip("` \n\t")

    start = raw.find("{")
    end = raw.rfind("}")
    if start < 0 or end < 0 or end <= start:
        return None
    snippet = raw[start : end + 1]
    try:
        return json.loads(snippet)
    except Exception:
        return None


def _to_bullets(text: str) -> str:
    raw = (text or "").strip()
    if not raw:
        return ""
    lines = [ln.strip("• \t") for ln in raw.splitlines() if ln.strip()]
    lines = [ln for ln in lines if ln]
    return "\n".join([f"• {ln}" for ln in lines])


async def ensure_articles_ingested(
    db: AsyncSession,
    *,
    lang: str,
    max_translate: int = 4,
    max_each_feed: int = 25,
) -> int:
    normalized_lang = _normalize_lang(lang)
    items = await fetch_feed_items(max_each=max_each_feed)
    if not items:
        return 0

    gemini = None
    if max_translate > 0 and _normalize_lang(lang) != "en":
        gemini_cfg = await get_gemini_config(db)
        gemini = GeminiClient(gemini_cfg) if gemini_cfg else None

    translated_count = 0
    inserted_count = 0

    for it in items:
        uid = _uid(it.title + it.url)

        insert_stmt = (
            insert(NewsArticle)
            .values(
                uid=uid,
                source=it.source,
                url=it.url,
                raw_title=it.title,
                raw_summary=it.summary or "",
                image_url=it.image_url or None,
                published_at=it.published_at,
                is_liquidation=it.is_liquidation,
            )
            .on_conflict_do_nothing(index_elements=["uid"])
            .returning(NewsArticle.id)
        )
        res = await db.execute(insert_stmt)
        article_id = res.scalar_one_or_none()
        if article_id is None:
            existing = await db.scalar(select(NewsArticle.id).where(NewsArticle.uid == uid))
            if existing is None:
                continue
            article_id = int(existing)
        else:
            inserted_count += 1

        if normalized_lang == "en":
            continue

        existing_translation = await db.scalar(
            select(NewsArticleTranslation.id)
            .where(NewsArticleTranslation.article_id == article_id)
            .where(NewsArticleTranslation.lang == normalized_lang)
        )
        if existing_translation is not None:
            continue

        if gemini is None or translated_count >= max_translate:
            continue

        translated = await _translate_item(gemini, it, normalized_lang)
        if translated is None:
            continue

        t_title, t_summary, model_used = translated
        await db.execute(
            insert(NewsArticleTranslation)
            .values(
                article_id=article_id,
                lang=normalized_lang,
                title=t_title,
                summary=t_summary,
                model=model_used,
            )
            .on_conflict_do_nothing(index_elements=["article_id", "lang"])
        )
        translated_count += 1

    if inserted_count > 0 or translated_count > 0:
        await db.commit()
    else:
        await db.flush()
    return translated_count


async def _get_or_create_state(db: AsyncSession, *, now: datetime) -> NewsFeedState:
    row = await db.scalar(select(NewsFeedState).where(NewsFeedState.id == 1))
    if row is not None:
        return row
    state = NewsFeedState(
        id=1,
        date=_today_str(now),
        daily_released_count=0,
        last_released_at=None,
        last_ingest_at=None,
        last_cleanup_at=None,
        last_translate_at=None,
    )
    db.add(state)
    await db.flush()
    await db.commit()
    return state


async def _maybe_cleanup_old_articles(db: AsyncSession, *, now: datetime) -> None:
    state = await _get_or_create_state(db, now=now)
    if state.last_cleanup_at is not None and (now - state.last_cleanup_at).total_seconds() < 6 * 60 * 60:
        return

    cutoff = now - timedelta(days=RETENTION_DAYS)
    await db.execute(
        delete(NewsArticle).where(
            func.coalesce(NewsArticle.published_at, NewsArticle.created_at) < cutoff
        )
    )
    await db.execute(
        update(NewsFeedState)
        .where(NewsFeedState.id == 1)
        .values(last_cleanup_at=now)
    )
    await db.commit()


async def _maybe_ingest(db: AsyncSession, *, now: datetime) -> None:
    state = await _get_or_create_state(db, now=now)
    if state.last_ingest_at is not None and (now - state.last_ingest_at).total_seconds() < INGEST_MIN_INTERVAL_SECONDS:
        return

    # Do network work outside any row lock. Inserts are idempotent.
    await ensure_articles_ingested(db, lang="en", max_translate=0, max_each_feed=20)
    await db.execute(update(NewsFeedState).where(NewsFeedState.id == 1).values(last_ingest_at=now))
    await db.commit()

async def _maybe_backfill_translations(
    db: AsyncSession,
    *,
    now: datetime,
    lang: str,
    max_items: int = 2,
) -> None:
    normalized_lang = _normalize_lang(lang)
    if normalized_lang == "en":
        return

    state = await _get_or_create_state(db, now=now)
    if state.last_translate_at is not None and (
        now - state.last_translate_at
    ).total_seconds() < TRANSLATE_BACKFILL_MIN_INTERVAL_SECONDS:
        return

    cfg = await get_gemini_config(db)
    if cfg is None:
        return
    gemini = GeminiClient(cfg)

    cutoff = now - timedelta(days=RETENTION_DAYS)
    missing_translation = ~select(1).where(
        (NewsArticleTranslation.article_id == NewsArticle.id)
        & (NewsArticleTranslation.lang == normalized_lang)
    ).exists()
    candidates = (
        await db.execute(
            select(NewsArticle)
            .where(NewsArticle.released_at.is_not(None))
            .where(func.coalesce(NewsArticle.published_at, NewsArticle.created_at) >= cutoff)
            .where(missing_translation)
            .order_by(NewsArticle.released_at.desc().nullslast(), NewsArticle.id.desc())
            .limit(max_items)
        )
    ).scalars().all()

    wrote = 0
    for a in candidates:
        translated = await _translate_item(
            gemini,
            FeedItem(
                source=a.source,
                title=a.raw_title,
                summary=a.raw_summary,
                url=a.url,
                image_url=a.image_url or "",
                published_at=a.published_at,
                is_liquidation=a.is_liquidation,
            ),
            normalized_lang,
        )
        if translated is None:
            continue
        t_title, t_summary, model_used = translated
        await db.execute(
            insert(NewsArticleTranslation)
            .values(
                article_id=a.id,
                lang=normalized_lang,
                title=t_title,
                summary=t_summary,
                model=model_used,
            )
            .on_conflict_do_nothing(index_elements=["article_id", "lang"])
        )
        wrote += 1

    await db.execute(update(NewsFeedState).where(NewsFeedState.id == 1).values(last_translate_at=now))
    if wrote > 0:
        await db.commit()
    else:
        await db.flush()


async def _release_next_if_due(
    db: AsyncSession,
    *,
    now: datetime,
    lang: str,
) -> None:
    await _get_or_create_state(db, now=now)

    # Lock state row to ensure hourly/daily constraints are applied atomically.
    state = await db.scalar(
        select(NewsFeedState).where(NewsFeedState.id == 1).with_for_update()
    )
    if state is None:
        return

    today = _today_str(now)
    if state.date != today:
        state.date = today
        state.daily_released_count = 0
        state.last_released_at = None

    if state.daily_released_count >= DAILY_MAX_RELEASED:
        await db.commit()
        return
    if state.last_released_at is not None and (now - state.last_released_at).total_seconds() < RELEASE_MIN_INTERVAL_SECONDS:
        await db.commit()
        return

    cutoff = now - timedelta(days=RETENTION_DAYS)
    candidate = await db.scalar(
        select(NewsArticle)
        .where(NewsArticle.released_at.is_(None))
        .where(func.coalesce(NewsArticle.published_at, NewsArticle.created_at) >= cutoff)
        .order_by(NewsArticle.published_at.desc().nullslast(), NewsArticle.id.desc())
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    if candidate is None:
        await db.commit()
        return

    candidate.released_at = now
    state.daily_released_count += 1
    state.last_released_at = now
    released_snapshot = FeedItem(
        source=candidate.source,
        title=candidate.raw_title,
        summary=candidate.raw_summary,
        url=candidate.url,
        image_url=candidate.image_url or "",
        published_at=candidate.published_at,
        is_liquidation=candidate.is_liquidation,
    )
    released_article_id = int(candidate.id)

    await db.commit()

    normalized_lang = _normalize_lang(lang)
    if normalized_lang == "en":
        return

    gemini_cfg = await get_gemini_config(db)
    gemini = GeminiClient(gemini_cfg) if gemini_cfg else None
    if gemini is None:
        return

    translated = await _translate_item(gemini, released_snapshot, normalized_lang)
    if translated is None:
        return

    t_title, t_summary, model_used = translated
    await db.execute(
        insert(NewsArticleTranslation)
        .values(
            article_id=released_article_id,
            lang=normalized_lang,
            title=t_title,
            summary=t_summary,
            model=model_used,
        )
        .on_conflict_do_nothing(index_elements=["article_id", "lang"])
    )
    await db.commit()


async def _translate_item(
    gemini: GeminiClient,
    item: FeedItem,
    lang: str,
) -> tuple[str, str, str] | None:
    source_text = _strip_html(item.summary)
    prompt_lang = "O'zbek" if lang == "uz" else ("Русский" if lang == "ru" else "English")

    prompt = f"""
Translate the following crypto news into {prompt_lang} in a professional tone.

Rules:
- Output MUST be valid JSON only (no markdown, no code fences).
- JSON keys: title, bullets, takeaway
- bullets: 3-7 bullet lines, each line must start with '• '.
- Keep it concise but meaningful. No English sentences if target is not English.

TITLE:
{item.title}

TEXT:
{source_text}
""".strip()

    res = await gemini.generate_text(prompt=prompt, temperature=0.2)
    if res is None:
        return None

    data = _extract_json_object(res.text) or {}
    title = (data.get("title") or "").strip()
    bullets = _to_bullets(str(data.get("bullets") or "").strip())
    takeaway = (data.get("takeaway") or "").strip()

    combined = bullets.strip()
    if takeaway:
        label = "Qisqa xulosa:" if lang == "uz" else ("Краткий вывод:" if lang == "ru" else "Takeaway:")
        combined = (combined + "\n\n" + f"{label} {takeaway}").strip() if combined else f"{label} {takeaway}"

    if not title:
        title = item.title.strip()
    if not combined:
        combined = _strip_html(item.summary or item.title)

    if lang != "en" and _looks_english(title + " " + combined):
        return None

    return title[:512], combined[:4000], res.model


async def build_news_feed_payload(
    db: AsyncSession,
    *,
    lang: str,
    limit: int,
) -> dict[str, object]:
    normalized_lang = _normalize_lang(lang)
    effective_limit = max(1, min(int(limit or 40), 50))

    now = _utc_now()
    await _maybe_cleanup_old_articles(db, now=now)
    await _maybe_ingest(db, now=now)
    await _release_next_if_due(db, now=now, lang=normalized_lang)
    await _maybe_backfill_translations(db, now=now, lang=normalized_lang, max_items=2)

    gemini_cfg = await get_gemini_config(db) if normalized_lang != "en" else None
    ai_enabled = bool(gemini_cfg)

    async def fetch_list(is_liquidation: bool) -> list[dict[str, str]]:
        articles = (
            await db.execute(
                select(NewsArticle)
                .where(NewsArticle.is_liquidation.is_(is_liquidation))
                .where(NewsArticle.released_at.is_not(None))
                .order_by(NewsArticle.released_at.desc().nullslast(), NewsArticle.id.desc())
                .limit(effective_limit * 2)
            )
        ).scalars().all()

        out: list[dict[str, str]] = []
        for a in articles:
            title = a.raw_title
            summary = a.raw_summary

            if normalized_lang != "en":
                t = await db.scalar(
                    select(NewsArticleTranslation)
                    .where(NewsArticleTranslation.article_id == a.id)
                    .where(NewsArticleTranslation.lang == normalized_lang)
                )
                if t is not None:
                    title = t.title
                    summary = t.summary

            published_at = a.published_at.isoformat() if a.published_at else ""
            out.append(
                {
                    "source": a.source,
                    "title": title,
                    "summary": summary,
                    "image": a.image_url or "",
                    "time": "",
                    "publishedAt": published_at,
                    "url": a.url,
                }
            )
            if len(out) >= effective_limit:
                break
        return out

    latest = await fetch_list(False)
    liq = await fetch_list(True)
    return {
        "latest": latest,
        "liquidations": liq,
        "updatedAt": now.isoformat(),
        "lang": normalized_lang,
        "aiEnabled": ai_enabled,
        "limit": effective_limit,
    }

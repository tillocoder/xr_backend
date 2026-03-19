from __future__ import annotations

import base64
import hashlib
import html as html_lib
import json
import re
import zlib
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
from time import mktime
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import feedparser
import httpx
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.entities import NewsArticle, NewsArticleTranslation, NewsFeedState
from app.services.ai_provider_config_service import get_gemini_config
from app.services.gemini_service import GeminiClient, build_gemini_client


DEFAULT_LANGS: tuple[str, ...] = ("en", "uz", "ru")
TARGET_LANGUAGE_NAMES = {"en": "English", "uz": "Uzbek", "ru": "Russian"}
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/135.0.0.0 Safari/537.36"
)
DEFAULT_HTTP_HEADERS = {
    "User-Agent": DEFAULT_USER_AGENT,
    "Accept": "application/rss+xml, application/xml, text/xml, application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
}
CRYPTOPANIC_POSTS_URL = "https://cryptopanic.com/web-api/posts/"
CRYPTOPANIC_NEWS_URL = "https://cryptopanic.com/news"
CRYPTOPANIC_SEED = "news"
CRYPTOPANIC_DECRYPT_KEY = b")b7Z*$+)/T}$9>/L"
PREVIEW_MAX_CHARS = 1800
PREVIEW_MAX_SENTENCES = 8
ARTICLE_SOURCE_MAX_CHARS = 2200
ARTICLE_SOURCE_MIN_CHARS = 700
ARTICLE_SOURCE_MIN_SENTENCES = 6
ARTICLE_PAGE_PARAGRAPH_LIMIT = 8


@dataclass(frozen=True)
class FeedProvider:
    source: str
    url: str
    source_lang: str = "en"
    translation_targets: tuple[str, ...] = ("uz", "ru")
    always_relevant: bool = True
    kind: str = "rss"


RSS_FEEDS: tuple[FeedProvider, ...] = (
    FeedProvider(source="The Daily Hodl", url="https://dailyhodl.com/feed/"),
    FeedProvider(source="Cointelegraph", url="https://cointelegraph.com/rss"),
    FeedProvider(source="NewsBTC", url="https://www.newsbtc.com/feed/"),
    FeedProvider(source="Bitcoin Magazine", url="https://bitcoinmagazine.com/feed"),
    FeedProvider(source="CryptoSlate", url="https://cryptoslate.com/feed/"),
    FeedProvider(source="CoinDesk", url="https://www.coindesk.com/arc/outboundfeeds/rss/"),
    FeedProvider(
        source="BeInCrypto",
        url="https://uz.beincrypto.com/feed/",
        source_lang="uz",
        translation_targets=("ru",),
    ),
    FeedProvider(
        source="CryptoPanic",
        url="https://cryptopanic.com/developers/api/posts/",
        kind="html",
    ),
)
RSS_FEED_BY_SOURCE = {provider.source.lower(): provider for provider in RSS_FEEDS}

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

CATEGORY_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("btc", (" bitcoin", " btc", " spot etf", " blackrock", " microstrategy")),
    ("eth", (" ethereum", " eth", " ether", " staking", " lido")),
    ("altcoins", (" solana", " sol ", " xrp", " altcoin", " ada", " cardano", " doge", " bnb", " ton", " trx")),
    ("macro", (" fed", " inflation", " rates", " cpi", " etf", " sec", " regulation", " congress", " treasury")),
)

INGEST_MIN_INTERVAL_SECONDS = 60 * 60
DAILY_MAX_FETCH_CYCLES = 24
RETENTION_DAYS = 10
TRANSLATE_BACKFILL_MIN_INTERVAL_SECONDS = 60 * 60
MAX_RELEASED_ARTICLES = 100
MAX_STORED_ARTICLES = 150
MAX_UNRELEASED_ARTICLES = MAX_STORED_ARTICLES - MAX_RELEASED_ARTICLES
INGEST_MAX_ITEMS_PER_FEED = 10
DUPLICATE_LOOKBACK_HOURS = 48
DUPLICATE_CANDIDATE_LIMIT = 50


@dataclass(frozen=True)
class FeedItem:
    source: str
    title: str
    summary: str
    url: str
    image_url: str
    published_at: datetime | None
    is_liquidation: bool
    guid: str
    category: str


@dataclass(slots=True)
class DuplicateCandidate:
    article_id: int
    source: str
    title: str
    summary: str
    url: str
    guid: str
    category: str
    published_at: datetime | None


@dataclass(slots=True)
class StoredNewsEntry:
    article_id: int
    uid: str
    source: str
    url: str
    image_url: str
    published_at: datetime | None
    released_at: datetime | None
    is_liquidation: bool
    category: str
    view_count: int
    translations: dict[str, dict[str, str]]

    def has_lang(self, lang: str) -> bool:
        normalized_lang = _normalize_lang(lang)
        payload = self.translations.get(normalized_lang) or {}
        return bool(str(payload.get("title") or "").strip() or str(payload.get("summary") or "").strip())

    def is_notification_ready(self) -> bool:
        return all(self.has_lang(lang) for lang in _required_langs_for_source(self.source))

    def to_payload_item(self, *, lang: str) -> dict[str, object]:
        normalized_lang = _normalize_lang(lang)
        localized = self.translations.get(normalized_lang)
        if localized is None:
            localized = self.translations.get("en")
        if localized is None and self.translations:
            localized = next(iter(self.translations.values()))
        localized = localized or {"title": "", "summary": ""}
        published_at = self.published_at.isoformat() if self.published_at else ""
        return {
            "id": self.article_id,
            "source": self.source,
            "title": str(localized.get("title") or "").strip(),
            "summary": str(localized.get("summary") or "").strip(),
            "image": self.image_url,
            "time": "",
            "publishedAt": published_at,
            "url": self.url,
            "category": self.category,
            "viewCount": self.view_count,
            "readMoreUrl": self.url,
        }

    def to_event_payload(self) -> dict[str, object]:
        published_at = self.published_at.isoformat() if self.published_at else ""
        return {
            "id": self.article_id,
            "uid": self.uid,
            "source": self.source,
            "url": self.url,
            "image": self.image_url,
            "publishedAt": published_at,
            "isLiquidation": self.is_liquidation,
            "category": self.category,
            "viewCount": self.view_count,
            "translations": {
                lang: {
                    "title": str(payload.get("title") or "").strip(),
                    "summary": str(payload.get("summary") or "").strip(),
                }
                for lang, payload in self.translations.items()
            },
        }


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _today_str(now: datetime | None = None) -> str:
    current = now or _utc_now()
    return current.date().isoformat()


def _uid(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:32]


def _strip_html(value: str) -> str:
    if not value:
        return ""
    cleaned = re.sub(r"<[^>]+>", " ", html_lib.unescape(value))
    return re.sub(r"\s+", " ", cleaned).strip()


def _looks_english(text: str) -> bool:
    normalized = f" {(text or '').lower()} "
    score = sum(word in normalized for word in [" the ", " and ", " to ", " in ", " of ", " is ", " are "])
    return score >= 3


def _normalize_lang(lang: str) -> str:
    base = (lang or "").strip().lower()
    if not base:
        return "en"
    base = base.split("-", 1)[0]
    if base in DEFAULT_LANGS:
        return base
    return "en"


def _provider_for_source(source: str) -> FeedProvider | None:
    return RSS_FEED_BY_SOURCE.get((source or "").strip().lower())


def _source_lang_for_source(source: str) -> str:
    provider = _provider_for_source(source)
    if provider is None:
        return "en"
    return _normalize_lang(provider.source_lang)


def _translation_targets_for_source(source: str) -> tuple[str, ...]:
    provider = _provider_for_source(source)
    if provider is None:
        return ("uz", "ru")
    return tuple(_normalize_lang(lang) for lang in provider.translation_targets)


def _required_langs_for_source(source: str) -> tuple[str, ...]:
    ordered_langs = (
        _source_lang_for_source(source),
        *_translation_targets_for_source(source),
    )
    unique_langs: list[str] = []
    seen: set[str] = set()
    for lang in ordered_langs:
        normalized_lang = _normalize_lang(lang)
        if normalized_lang in seen:
            continue
        seen.add(normalized_lang)
        unique_langs.append(normalized_lang)
    return tuple(unique_langs)


def _article_summary(summary: str, title: str) -> str:
    normalized = _clip_preview_text(summary)
    if normalized:
        return normalized
    return _clip_preview_text(title)


def _clip_preview_text(value: str, *, max_chars: int = PREVIEW_MAX_CHARS) -> str:
    normalized = _sanitize_preview_text(value)
    if not normalized:
        return ""
    if len(normalized) <= max_chars:
        return normalized

    sentences = [
        sentence.strip()
        for sentence in re.split(r"(?<=[\.\!\?])\s+", normalized)
        if sentence.strip()
    ]
    if not sentences:
        return normalized[:max_chars].rstrip(" ,;:-") + "..."

    selected: list[str] = []
    total_length = 0
    for sentence in sentences:
        extra = len(sentence) + (1 if selected else 0)
        if selected and total_length + extra > max_chars:
            break
        selected.append(sentence)
        total_length += extra
        if len(selected) >= PREVIEW_MAX_SENTENCES and total_length >= int(max_chars * 0.6):
            break

    clipped = " ".join(selected).strip()
    if not clipped:
        clipped = normalized[:max_chars].rstrip(" ,;:-")
    if len(clipped) < len(normalized):
        return clipped.rstrip(" ,;:-") + "..."
    return clipped


def _sanitize_preview_text(value: str) -> str:
    normalized = _strip_html(value)
    if not normalized:
        return ""
    normalized = re.sub(r"continue reading:?.*$", "", normalized, flags=re.I)
    normalized = re.sub(r"\*?\s*this is not investment advice\.?", "", normalized, flags=re.I)
    normalized = re.sub(r"\b(read more|source):\s+https?://\S+", "", normalized, flags=re.I)
    return re.sub(r"\s+", " ", normalized).strip(" -\n\r\t")


def _sentence_count(value: str) -> int:
    normalized = _sanitize_preview_text(value)
    if not normalized:
        return 0
    return len(
        [
            sentence
            for sentence in re.split(r"(?<=[\.\!\?])\s+", normalized)
            if sentence.strip()
        ]
    )


def _summary_has_enough_detail(value: str) -> bool:
    normalized = _sanitize_preview_text(value)
    if len(normalized) >= ARTICLE_SOURCE_MAX_CHARS:
        return True
    if len(normalized) < ARTICLE_SOURCE_MIN_CHARS:
        return False
    return _sentence_count(normalized) >= ARTICLE_SOURCE_MIN_SENTENCES


def _looks_like_market_strip(value: str) -> bool:
    normalized = re.sub(r"\s+", " ", str(value or "")).strip()
    if not normalized:
        return False
    percent_count = normalized.count("%")
    dollar_count = normalized.count("$")
    ticker_count = len(re.findall(r"\b[A-Z]{2,6}\b", normalized))
    return (percent_count >= 3 and ticker_count >= 4) or (dollar_count >= 2 and ticker_count >= 4)


def _is_low_signal_paragraph(value: str, *, title: str = "") -> bool:
    normalized = _sanitize_preview_text(value)
    if len(normalized) < 60:
        return True
    lowered = normalized.lower()
    if lowered.startswith(("related:", "read more", "continue reading", "source:")):
        return True
    if "follow us" in lowered or "subscribe" in lowered:
        return True
    if _looks_like_market_strip(normalized):
        return True
    if title:
        title_ratio = SequenceMatcher(None, _normalize_text(normalized), _normalize_text(title)).ratio()
        if title_ratio >= 0.92:
            return True
    return False


def _extract_entry_preview_text(entry) -> str:
    candidates: list[str] = []

    content = getattr(entry, "content", None) or []
    if isinstance(content, list):
        for chunk in content:
            if not isinstance(chunk, dict):
                continue
            value = _sanitize_preview_text(chunk.get("value") or "")
            if value:
                candidates.append(value)

    summary_value = _sanitize_preview_text(getattr(entry, "summary", "") or "")
    if summary_value:
        candidates.append(summary_value)

    if not candidates:
        return ""

    return _clip_preview_text(
        max(candidates, key=len),
        max_chars=ARTICLE_SOURCE_MAX_CHARS,
    )


def _extract_article_page_preview_text(html: str, *, title: str = "") -> str:
    if not html:
        return ""

    normalized_html = re.sub(
        r"<(script|style|noscript|svg|footer|header|nav|aside)[^>]*>.*?</\1>",
        " ",
        html,
        flags=re.I | re.S,
    )
    paragraphs = re.findall(r"<p[^>]*>(.*?)</p>", normalized_html, flags=re.I | re.S)

    selected: list[str] = []
    seen: set[str] = set()
    total_length = 0
    for paragraph in paragraphs:
        cleaned = _sanitize_preview_text(paragraph)
        if not cleaned:
            continue
        if _is_low_signal_paragraph(cleaned, title=title):
            continue
        key = _normalize_text(cleaned)
        if not key or key in seen:
            continue
        seen.add(key)
        selected.append(cleaned)
        total_length += len(cleaned)
        if len(selected) >= ARTICLE_PAGE_PARAGRAPH_LIMIT or total_length >= ARTICLE_SOURCE_MAX_CHARS:
            break

    if not selected:
        return ""

    return _clip_preview_text(
        " ".join(selected),
        max_chars=ARTICLE_SOURCE_MAX_CHARS,
    )


async def _fetch_article_page_preview_text(url: str, *, title: str = "") -> str:
    normalized_url = _canonicalize_url(url)
    if not normalized_url:
        return ""
    timeout = httpx.Timeout(connect=8, read=18, write=10, pool=10)
    limits = httpx.Limits(max_connections=4, max_keepalive_connections=2)
    try:
        async with httpx.AsyncClient(
            timeout=timeout,
            limits=limits,
            headers=DEFAULT_HTTP_HEADERS,
            follow_redirects=True,
        ) as client:
            response = await client.get(normalized_url)
    except Exception:
        return ""
    if response.status_code >= 400:
        return ""
    return _extract_article_page_preview_text(response.text or "", title=title)


async def _build_enriched_source_summary(*, summary: str, title: str, url: str) -> str:
    base_summary = _article_summary(summary, title)
    if _summary_has_enough_detail(base_summary):
        return base_summary

    page_summary = await _fetch_article_page_preview_text(url, title=title)
    if not page_summary:
        return base_summary
    if len(page_summary) <= len(base_summary) and _sentence_count(page_summary) <= _sentence_count(base_summary):
        return base_summary
    return page_summary


def _parse_iso_datetime(value: object) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(raw)
    except Exception:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _matches_keywords(text: str, keywords: list[str]) -> bool:
    normalized = f" {(text or '').lower()} "
    return any(keyword in normalized for keyword in keywords)


def _normalize_text(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9$% ]+", " ", (value or "").lower())
    return re.sub(r"\s+", " ", normalized).strip()


def _important_tokens(value: str) -> set[str]:
    tokens = {
        token
        for token in _normalize_text(value).split()
        if len(token) >= 3 and token not in {"with", "from", "that", "this", "into", "amid", "after", "over"}
    }
    return tokens


def _token_overlap_score(left: str, right: str) -> float:
    left_tokens = _important_tokens(left)
    right_tokens = _important_tokens(right)
    if not left_tokens or not right_tokens:
        return 0.0
    common = left_tokens & right_tokens
    return len(common) / max(len(left_tokens), len(right_tokens))


def _shared_numeric_tokens(left: str, right: str) -> set[str]:
    left_values = set(re.findall(r"\$?\d[\d,\.]*", left or ""))
    right_values = set(re.findall(r"\$?\d[\d,\.]*", right or ""))
    return left_values & right_values


def _detect_category(text: str, *, is_liquidation: bool) -> str:
    if is_liquidation:
        return "liquidation"
    normalized = f" {_normalize_text(text)} "
    for category, keywords in CATEGORY_KEYWORDS:
        if any(keyword in normalized for keyword in keywords):
            return category
    return "altcoins"


def _canonicalize_url(url: str) -> str:
    raw = (url or "").strip()
    if not raw:
        return ""
    try:
        parsed = urlparse(raw)
    except Exception:
        return raw
    filtered_query = []
    for key, value in parse_qsl(parsed.query, keep_blank_values=True):
        key_norm = key.strip().lower()
        if key_norm.startswith("utm_") or key_norm in {"fbclid", "gclid", "ref", "source"}:
            continue
        filtered_query.append((key, value))
    canonical = parsed._replace(
        query=urlencode(filtered_query, doseq=True),
        fragment="",
        scheme=(parsed.scheme or "https").lower(),
        netloc=parsed.netloc.lower(),
    )
    return urlunparse(canonical)


def _extract_feed_guid(entry) -> str:
    raw = (
        getattr(entry, "id", None)
        or getattr(entry, "guid", None)
        or getattr(entry, "guidislink", None)
        or getattr(entry, "link", None)
        or ""
    )
    return str(raw or "").strip()


def _pick_image(entry) -> str:
    candidates: list[str] = []
    for enc in getattr(entry, "enclosures", []) or []:
        url = (enc.get("href") or enc.get("url") or "").strip()
        if url:
            candidates.append(url)
    for media in getattr(entry, "media_content", []) or []:
        url = (media.get("url") or "").strip()
        if url:
            candidates.append(url)
    for media in getattr(entry, "media_thumbnail", []) or []:
        url = (media.get("url") or "").strip()
        if url:
            candidates.append(url)

    content = getattr(entry, "content", None) or []
    if isinstance(content, list):
        for chunk in content:
            if not isinstance(chunk, dict):
                continue
            html = (chunk.get("value") or "").strip()
            image_url = _extract_first_image_from_html(html)
            if image_url:
                candidates.append(image_url)

    summary_raw = (getattr(entry, "summary", "") or "").strip()
    summary_image = _extract_first_image_from_html(summary_raw)
    if summary_image:
        candidates.append(summary_image)

    return _canonicalize_url(candidates[0]) if candidates else ""


def _extract_first_image_from_html(html: str) -> str:
    if not html:
        return ""
    match = re.search(r'<img[^>]+src=["\']([^"\']+)["\']', html, flags=re.I)
    if match:
        return (match.group(1) or "").strip()
    match = re.search(r'srcset=["\']([^"\']+)["\']', html, flags=re.I)
    if match:
        raw = (match.group(1) or "").strip()
        first = raw.split(",", 1)[0].strip()
        return first.split(" ", 1)[0].strip()
    return ""


def _entry_published_at(entry) -> datetime | None:
    parsed_time = getattr(entry, "published_parsed", None) or getattr(entry, "updated_parsed", None)
    if not parsed_time:
        return None
    try:
        return datetime.fromtimestamp(mktime(parsed_time), tz=timezone.utc)
    except Exception:
        return None


def _feed_item_dedupe_key(item: FeedItem) -> str:
    return f"{item.source.lower()}::{item.guid or item.url or _normalize_text(item.title)}"


def _item_identity_value(item: FeedItem) -> str:
    return item.guid or item.url or _normalize_text(item.title)


async def _fetch_feed_xml(url: str) -> str:
    timeout = httpx.Timeout(connect=8, read=16, write=10, pool=10)
    limits = httpx.Limits(max_connections=8, max_keepalive_connections=4)
    async with httpx.AsyncClient(
        timeout=timeout,
        limits=limits,
        headers=DEFAULT_HTTP_HEADERS,
        follow_redirects=True,
    ) as client:
        response = await client.get(url)
        if response.status_code >= 400:
            return ""
        return (response.text or "").strip()


async def _fetch_rss_provider_items(
    provider: FeedProvider,
    *,
    max_each: int,
) -> list[FeedItem]:
    try:
        xml = await _fetch_feed_xml(provider.url)
    except Exception:
        return []
    if not xml:
        return []
    parsed = feedparser.parse(xml)
    items: list[FeedItem] = []
    for entry in (parsed.entries or [])[: max(1, int(max_each))]:
        title = (getattr(entry, "title", "") or "").strip()
        link = _canonicalize_url((getattr(entry, "link", "") or "").strip())
        if not title or not link:
            continue
        summary = _extract_entry_preview_text(entry)
        text = f"{title} {summary}".strip()
        is_liquidation = _matches_keywords(text, LIQ_KEYWORDS)
        is_relevant = provider.always_relevant or is_liquidation or _matches_keywords(text, TOPIC_KEYWORDS)
        if not is_relevant:
            continue
        items.append(
            FeedItem(
                source=provider.source,
                title=title,
                summary=summary,
                url=link,
                image_url=_pick_image(entry),
                published_at=_entry_published_at(entry),
                is_liquidation=is_liquidation,
                guid=_extract_feed_guid(entry) or link,
                category=_detect_category(text, is_liquidation=is_liquidation),
            )
        )
    return items


def _decrypt_cryptopanic_rows(
    *,
    encrypted_payload: str,
    csrf_token: str,
) -> list[dict[str, object]]:
    encoded = str(encrypted_payload or "").strip()
    nonce_suffix = str(csrf_token or "").strip()
    if not encoded or not nonce_suffix:
        return []

    iv = (CRYPTOPANIC_SEED + nonce_suffix)[:16].encode("utf-8")
    if len(iv) != 16:
        return []

    try:
        cipher = Cipher(algorithms.AES(CRYPTOPANIC_DECRYPT_KEY), modes.CBC(iv))
        decryptor = cipher.decryptor()
        decrypted = decryptor.update(base64.b64decode(encoded)) + decryptor.finalize()
        decompressed = zlib.decompress(decrypted.rstrip(b"\x00"))
        payload = json.loads(decompressed.decode("utf-8"))
    except Exception:
        return []

    keys = payload.get("k")
    rows = payload.get("l")
    if not isinstance(keys, list) or not isinstance(rows, list):
        return []

    normalized_rows: list[dict[str, object]] = []
    for row in rows:
        if not isinstance(row, list):
            continue
        item = {
            str(keys[index]): row[index]
            for index in range(min(len(keys), len(row)))
            if keys[index] is not None
        }
        normalized_rows.append(item)
    return normalized_rows


def _pick_cryptopanic_summary(row: dict[str, object]) -> str:
    content = row.get("content")
    candidates: list[str] = []
    if isinstance(content, dict):
        for key in ("clean_v2", "original_v2"):
            value = _sanitize_preview_text(str(content.get(key) or ""))
            if value:
                candidates.append(value)
    body = _sanitize_preview_text(str(row.get("body") or ""))
    if body:
        candidates.insert(0, body)

    for candidate in candidates:
        if candidate:
            return _clip_preview_text(candidate)
    return ""


async def _fetch_cryptopanic_items(*, max_each: int) -> list[FeedItem]:
    timeout = httpx.Timeout(connect=8, read=16, write=10, pool=10)
    limits = httpx.Limits(max_connections=4, max_keepalive_connections=2)
    filters: dict[str, object] = {"module": "news"}
    settings = get_settings()
    if settings.cryptopanic_api_token:
        filters["auth_token"] = settings.cryptopanic_api_token

    async with httpx.AsyncClient(
        timeout=timeout,
        limits=limits,
        follow_redirects=True,
        headers={
            **DEFAULT_HTTP_HEADERS,
            "Accept": "application/json, text/plain, */*",
            "Referer": CRYPTOPANIC_NEWS_URL,
            "X-Requested-With": "XMLHttpRequest",
        },
    ) as client:
        try:
            page = await client.get(CRYPTOPANIC_NEWS_URL)
        except Exception:
            return []
        if page.status_code >= 400:
            return []

        csrf_token = str(client.cookies.get("csrftoken") or "").strip()
        if not csrf_token:
            return []

        try:
            response = await client.post(
                CRYPTOPANIC_POSTS_URL,
                headers={"X-CSRFToken": csrf_token},
                data={"filters": json.dumps(filters, separators=(",", ":"))},
            )
        except Exception:
            return []
        if response.status_code >= 400:
            return []

    try:
        payload = response.json()
    except Exception:
        return []
    if payload.get("status") is not True:
        return []

    rows = _decrypt_cryptopanic_rows(
        encrypted_payload=str(payload.get("s") or ""),
        csrf_token=csrf_token,
    )
    items: list[FeedItem] = []
    seen: set[str] = set()
    for row in rows:
        if str(row.get("kind") or "").strip().lower() not in {"link", "post"}:
            continue
        title = _sanitize_preview_text(str(row.get("title") or ""))
        link = _canonicalize_url(str(row.get("url") or row.get("remote_id") or "").strip())
        if not title or not link:
            continue
        summary = _pick_cryptopanic_summary(row)
        text = f"{title} {summary}".strip()
        is_liquidation = _matches_keywords(text, LIQ_KEYWORDS)
        if not _matches_keywords(text, TOPIC_KEYWORDS) and not is_liquidation:
            continue

        guid = str(row.get("pk") or row.get("remote_id") or link).strip()
        dedupe_key = f"{guid}|{link}"
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)

        items.append(
            FeedItem(
                source="CryptoPanic",
                title=title,
                summary=summary,
                url=link,
                image_url=_canonicalize_url(str(row.get("image") or "").strip()),
                published_at=_parse_iso_datetime(row.get("published_at")),
                is_liquidation=is_liquidation,
                guid=guid or link,
                category=_detect_category(text, is_liquidation=is_liquidation),
            )
        )
        if len(items) >= max(1, int(max_each)):
            break
    return items


async def fetch_feed_items(*, max_each: int = INGEST_MAX_ITEMS_PER_FEED) -> list[FeedItem]:
    results: list[FeedItem] = []

    async def fetch_one(provider: FeedProvider) -> None:
        if provider.kind == "html":
            items = await _fetch_cryptopanic_items(max_each=max_each)
        else:
            items = await _fetch_rss_provider_items(provider, max_each=max_each)
        results.extend(items)

    await _gather_safely([fetch_one(provider) for provider in RSS_FEEDS])
    results.sort(key=lambda item: item.published_at or datetime.fromtimestamp(0, tz=timezone.utc), reverse=True)

    deduped: list[FeedItem] = []
    seen: set[str] = set()
    for item in results:
        key = _feed_item_dedupe_key(item)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped


async def _gather_safely(tasks):
    import asyncio

    return await asyncio.gather(*tasks, return_exceptions=True)


def _extract_json_object(text: str) -> dict | None:
    raw = (text or "").strip()
    if not raw:
        return None
    normalized = raw.replace("```json", "```").replace("```JSON", "```").strip("` \n\t")
    start = normalized.find("{")
    end = normalized.rfind("}")
    if start < 0 or end < 0 or end <= start:
        return None
    snippet = normalized[start : end + 1]
    try:
        return json.loads(snippet)
    except Exception:
        return None


def _to_bullets(value: object) -> str:
    if isinstance(value, (list, tuple)):
        raw_lines = [str(item or "").strip() for item in value if str(item or "").strip()]
    else:
        raw = str(value or "").strip()
        if not raw:
            return ""
        raw_lines = [line.strip() for line in raw.splitlines() if line.strip()]

    lines: list[str] = []
    for line in raw_lines:
        normalized = re.sub(r"^(?:[-*])\s*", "", line).lstrip("\u2022").strip()
        if normalized:
            lines.append(normalized)
    return "\n".join([f"- {line}" for line in lines])


def _article_to_feed_item(article: NewsArticle) -> FeedItem:
    return FeedItem(
        source=article.source,
        title=article.raw_title,
        summary=_article_summary(article.raw_summary, article.raw_title),
        url=article.url,
        image_url=article.image_url or "",
        published_at=article.published_at,
        is_liquidation=article.is_liquidation,
        guid=(article.source_guid or article.url),
        category=article.category,
    )


def _article_to_duplicate_candidate(article: NewsArticle) -> DuplicateCandidate:
    return DuplicateCandidate(
        article_id=int(article.id),
        source=str(article.source),
        title=str(article.raw_title),
        summary=_article_summary(article.raw_summary, article.raw_title),
        url=str(article.url),
        guid=str(article.source_guid or ""),
        category=str(article.category or "altcoins"),
        published_at=article.published_at,
    )


def _feed_item_to_duplicate_candidate(item: FeedItem, article_id: int) -> DuplicateCandidate:
    return DuplicateCandidate(
        article_id=article_id,
        source=item.source,
        title=item.title,
        summary=_article_summary(item.summary, item.title),
        url=item.url,
        guid=item.guid,
        category=item.category,
        published_at=item.published_at,
    )


def _compose_translations(
    article: NewsArticle,
    translation_rows: list[NewsArticleTranslation],
) -> dict[str, dict[str, str]]:
    raw_title = (article.raw_title or "").strip()
    raw_summary = _article_summary(article.raw_summary, article.raw_title)
    source_lang = _source_lang_for_source(article.source)
    translations: dict[str, dict[str, str]] = {}
    if raw_title or raw_summary:
        translations[source_lang] = {"title": raw_title, "summary": raw_summary}
    for row in translation_rows:
        lang = _normalize_lang(row.lang)
        title = (row.title or "").strip() or raw_title
        summary = (row.summary or "").strip() or raw_summary
        if title or summary:
            translations[lang] = {"title": title, "summary": summary}
    if "en" not in translations and source_lang == "en":
        translations["en"] = {"title": raw_title, "summary": raw_summary}
    return translations


async def _load_recent_duplicate_candidates(db: AsyncSession) -> list[DuplicateCandidate]:
    cutoff = _utc_now() - timedelta(hours=DUPLICATE_LOOKBACK_HOURS)
    rows = (
        await db.execute(
            select(NewsArticle)
            .where(func.coalesce(NewsArticle.published_at, NewsArticle.created_at) >= cutoff)
            .order_by(func.coalesce(NewsArticle.published_at, NewsArticle.created_at).desc(), NewsArticle.id.desc())
            .limit(DUPLICATE_CANDIDATE_LIMIT)
        )
    ).scalars().all()
    return [_article_to_duplicate_candidate(article) for article in rows]


def _heuristic_duplicate_score(item: FeedItem, candidate: DuplicateCandidate) -> float:
    title_ratio = SequenceMatcher(None, _normalize_text(item.title), _normalize_text(candidate.title)).ratio()
    summary_ratio = SequenceMatcher(
        None,
        _normalize_text(_article_summary(item.summary, item.title)),
        _normalize_text(candidate.summary),
    ).ratio()
    overlap = _token_overlap_score(
        f"{item.title} {_article_summary(item.summary, item.title)}",
        f"{candidate.title} {candidate.summary}",
    )
    numeric_overlap = 0.15 if _shared_numeric_tokens(item.title + " " + item.summary, candidate.title + " " + candidate.summary) else 0.0
    category_bonus = 0.08 if item.category == candidate.category else 0.0
    return max(title_ratio, summary_ratio * 0.85 + overlap * 0.35 + numeric_overlap + category_bonus)


async def _ai_duplicate_check(
    gemini: GeminiClient,
    *,
    item: FeedItem,
    candidate: DuplicateCandidate,
) -> bool:
    prompt = f"""
Decide if these two crypto news posts are materially the same news event.

Return JSON only:
{{"duplicate": true/false}}

Post A source: {item.source}
Post A title: {item.title}
Post A summary: {_article_summary(item.summary, item.title)}

Post B source: {candidate.source}
Post B title: {candidate.title}
Post B summary: {candidate.summary}
""".strip()

    result = await gemini.generate_text(prompt=prompt, temperature=0.0)
    if result is None:
        return False
    data = _extract_json_object(result.text) or {}
    return data.get("duplicate") is True


async def _is_duplicate_story(
    *,
    item: FeedItem,
    recent_candidates: list[DuplicateCandidate],
    gemini: GeminiClient | None,
) -> bool:
    for candidate in recent_candidates:
        if candidate.url and candidate.url == item.url:
            return True
        if candidate.guid and item.guid and candidate.guid == item.guid:
            return True

    ranked: list[tuple[float, DuplicateCandidate]] = []
    for candidate in recent_candidates:
        score = _heuristic_duplicate_score(item, candidate)
        if score >= 0.58:
            ranked.append((score, candidate))
    ranked.sort(key=lambda pair: pair[0], reverse=True)

    for score, candidate in ranked[:3]:
        if score >= 0.9:
            return True
        if gemini is None:
            if score >= 0.83:
                return True
            continue
        try:
            if await _ai_duplicate_check(gemini, item=item, candidate=candidate):
                return True
        except Exception:
            if score >= 0.83:
                return True
    return False


async def ensure_articles_ingested(
    db: AsyncSession,
    *,
    max_each_feed: int = INGEST_MAX_ITEMS_PER_FEED,
    enable_ai_dedup: bool = False,
) -> int:
    items = await fetch_feed_items(max_each=max_each_feed)
    if not items:
        return 0

    recent_candidates = await _load_recent_duplicate_candidates(db)
    gemini = await build_gemini_client(db) if enable_ai_dedup else None

    inserted_count = 0
    for item in items:
        identity = _item_identity_value(item)
        article_uid = _uid(f"{item.source}|{identity}")
        existing_id = await db.scalar(select(NewsArticle.id).where(NewsArticle.uid == article_uid))
        if existing_id is not None:
            continue

        if await _is_duplicate_story(item=item, recent_candidates=recent_candidates, gemini=gemini):
            continue

        insert_stmt = (
            insert(NewsArticle)
            .values(
                uid=article_uid,
                source=item.source,
                source_guid=item.guid or None,
                url=item.url,
                raw_title=item.title,
                raw_summary=item.summary or "",
                image_url=item.image_url or None,
                published_at=item.published_at,
                category=item.category,
                is_liquidation=item.is_liquidation,
            )
            .on_conflict_do_nothing()
            .returning(NewsArticle.id)
        )
        result = await db.execute(insert_stmt)
        inserted_id = result.scalar_one_or_none()
        if inserted_id is None:
            continue
        inserted_count += 1
        recent_candidates.insert(0, _feed_item_to_duplicate_candidate(item, int(inserted_id)))
        recent_candidates[:] = recent_candidates[:DUPLICATE_CANDIDATE_LIMIT]

    trimmed_count = await _trim_stored_articles(db, keep=MAX_STORED_ARTICLES)
    if inserted_count > 0 or trimmed_count > 0:
        await db.commit()
    else:
        await db.flush()
    return inserted_count


async def _ensure_article_translations(
    db: AsyncSession,
    *,
    article: NewsArticle,
    gemini: GeminiClient | None = None,
) -> int:
    enriched_summary = await _build_enriched_source_summary(
        summary=article.raw_summary or "",
        title=article.raw_title or "",
        url=article.url or "",
    )
    current_summary = _sanitize_preview_text(article.raw_summary or "")
    if enriched_summary and enriched_summary != current_summary:
        article.raw_summary = enriched_summary
        await db.flush()

    target_langs = _translation_targets_for_source(article.source)
    if not target_langs:
        return 0

    rows = (
        await db.execute(
            select(NewsArticleTranslation).where(NewsArticleTranslation.article_id == article.id)
        )
    ).scalars().all()
    existing_langs = {_normalize_lang(row.lang) for row in rows}
    missing_langs = [lang for lang in target_langs if lang not in existing_langs]
    if not missing_langs:
        return 0

    gemini_client = gemini or await build_gemini_client(db)
    if gemini_client is None:
        return 0

    snapshot = _article_to_feed_item(article)
    wrote = 0
    for lang in missing_langs:
        translated = await _translate_item(gemini_client, snapshot, lang)
        if translated is None:
            continue
        title, summary, model_used = translated
        await db.execute(
            insert(NewsArticleTranslation)
            .values(
                article_id=article.id,
                lang=lang,
                title=title,
                summary=summary,
                model=model_used,
            )
            .on_conflict_do_nothing(index_elements=["article_id", "lang"])
        )
        wrote += 1
    return wrote


async def _ensure_article_translations_for_id(
    db: AsyncSession,
    *,
    article_id: int,
) -> int:
    article = await db.get(NewsArticle, int(article_id))
    if article is None:
        return 0
    wrote = await _ensure_article_translations(db, article=article)
    if wrote > 0:
        await db.commit()
    else:
        await db.flush()
    return wrote


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


async def _maybe_cleanup_old_articles(db: AsyncSession, *, now: datetime) -> None:
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


async def _trim_stored_articles(db: AsyncSession, *, keep: int) -> int:
    keep_total = max(1, int(keep))
    keep_released = max(1, min(MAX_RELEASED_ARTICLES, keep_total))
    keep_unreleased = max(0, min(MAX_UNRELEASED_ARTICLES, keep_total - keep_released))
    stale_ids: set[int] = set()

    released_ids = (
        await db.scalars(
            select(NewsArticle.id)
            .where(NewsArticle.released_at.is_not(None))
            .order_by(NewsArticle.released_at.desc().nullslast(), NewsArticle.id.desc())
            .offset(keep_released)
        )
    ).all()
    stale_ids.update(int(article_id) for article_id in released_ids)

    unreleased_ids = (
        await db.scalars(
            select(NewsArticle.id)
            .where(NewsArticle.released_at.is_(None))
            .order_by(
                func.coalesce(NewsArticle.published_at, NewsArticle.created_at).desc(),
                NewsArticle.id.desc(),
            )
            .offset(keep_unreleased)
        )
    ).all()
    stale_ids.update(int(article_id) for article_id in unreleased_ids)

    overflow_ids = (
        await db.scalars(
            select(NewsArticle.id)
            .order_by(
                func.coalesce(
                    NewsArticle.released_at,
                    NewsArticle.published_at,
                    NewsArticle.created_at,
                ).desc(),
                NewsArticle.id.desc(),
            )
            .offset(keep_total)
        )
    ).all()
    stale_ids.update(int(article_id) for article_id in overflow_ids)

    if not stale_ids:
        return 0
    result = await db.execute(delete(NewsArticle).where(NewsArticle.id.in_(sorted(stale_ids))))
    return int(result.rowcount or 0)


async def _maybe_ingest(db: AsyncSession, *, now: datetime) -> None:
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


async def _release_pending_articles(
    db: AsyncSession,
    *,
    now: datetime,
) -> list[int]:
    state = await _get_or_create_state(db, now=now)
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
        .limit(1)
    )
    if state.last_released_at is not None:
        # Only the newest article since the previous release is eligible.
        # Older backlog items stay unreleased and are eventually trimmed.
        candidate_stmt = candidate_stmt.where(candidate_sort_key > state.last_released_at)

    candidate = await db.scalar(candidate_stmt)
    if candidate is None:
        await db.flush()
        return []

    gemini = await build_gemini_client(db)
    if gemini is None:
        return []
    await _ensure_article_translations(db, article=candidate, gemini=gemini)

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
        image_url=str(candidate.image_url or ""),
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


async def _translate_item(
    gemini: GeminiClient,
    item: FeedItem,
    lang: str,
) -> tuple[str, str, str] | None:
    normalized_lang = _normalize_lang(lang)
    source_text = _clip_preview_text(
        _sanitize_preview_text(item.summary),
        max_chars=ARTICLE_SOURCE_MAX_CHARS,
    )
    if not source_text:
        source_text = _clip_preview_text(item.title, max_chars=ARTICLE_SOURCE_MAX_CHARS)
    prompt_lang = TARGET_LANGUAGE_NAMES.get(normalized_lang, "English")
    prompt = f"""
Translate this crypto news into {prompt_lang}.

Return JSON only with these keys:
- title
- highlights

Rules:
- Do not wrap the JSON in markdown or code fences.
- highlights must be a JSON array with 6 to 8 detailed bullet points.
- Each bullet should be an informative full sentence. A second short sentence is allowed only if it adds an important fact.
- Preserve numbers, tickers, company names, and market terms accurately.
- If the source is already in the target language, rewrite it cleanly in that language.
- Do not leave English sentences in non-English output.
- Use as many concrete facts as the body supports: amounts, dates, stages, who did what, and likely market impact.
- Cover the article broadly instead of only the top 2 or 3 facts.
- Do not add a generic short conclusion or takeaway line.
- Do not repeat the same fact in different words.

Title:
{item.title}

Body:
{source_text}
""".strip()

    result = await gemini.generate_text(prompt=prompt, temperature=0.2)
    if result is None:
        return None

    data = _extract_json_object(result.text) or {}
    title = (data.get("title") or "").strip()
    combined = _to_bullets(data.get("highlights") or data.get("bullets")).strip()

    if not title:
        title = item.title.strip()
    if not combined:
        combined = source_text

    if normalized_lang != "en" and _looks_english(f"{title} {combined}"):
        return None
    return title[:512], combined[:2600], result.model


async def run_news_pipeline(
    db: AsyncSession,
    *,
    now: datetime | None = None,
) -> None:
    current_now = now or _utc_now()
    await _maybe_cleanup_old_articles(db, now=current_now)
    await _maybe_ingest(db, now=current_now)
    await _release_pending_articles(db, now=current_now)


def _apply_news_sort(stmt, *, sort: str):
    if sort == "trending":
        return stmt.order_by(
            NewsArticle.view_count.desc(),
            NewsArticle.released_at.desc().nullslast(),
            NewsArticle.id.desc(),
        )
    return stmt.order_by(NewsArticle.released_at.desc().nullslast(), NewsArticle.id.desc())


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
                image_url=str(article.image_url or ""),
                published_at=article.published_at,
                released_at=article.released_at,
                is_liquidation=article.is_liquidation,
                category=str(article.category or "altcoins"),
                view_count=int(article.view_count or 0),
                translations=_compose_translations(article, rows_by_article.get(int(article.id), [])),
            )
        )
    return entries


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
                    .where(NewsArticle.released_at.is_not(None))
                    .order_by(NewsArticle.released_at.desc().nullslast(), NewsArticle.id.desc())
                    .limit(200)
                )
            ).scalars().all()
            for candidate in candidates:
                if _canonicalize_url(candidate.url) == normalized_url:
                    article = candidate
                    break
    if article is None or article.released_at is None:
        return None

    article.view_count = int(article.view_count or 0) + 1
    await db.commit()
    return int(article.view_count)


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

    total = await count_released_news_entries(
        db,
        is_liquidation=False,
        category=normalized_category,
    )
    entries = await load_released_news_entries(
        db,
        limit=normalized_page_size,
        offset=offset,
        sort=normalized_sort,
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
        "updatedAt": _utc_now().isoformat(),
    }


async def build_news_feed_payload(
    db: AsyncSession,
    *,
    lang: str,
    limit: int,
) -> dict[str, object]:
    normalized_lang = _normalize_lang(lang)
    effective_limit = max(1, min(int(limit or 12), 20))

    latest_entries = await load_released_news_entries(
        db,
        limit=effective_limit,
        sort="latest",
        is_liquidation=False,
    )
    liquidation_entries = await load_released_news_entries(
        db,
        limit=effective_limit,
        sort="latest",
        is_liquidation=True,
    )

    latest = [entry.to_payload_item(lang=normalized_lang) for entry in latest_entries]
    liquidations = [entry.to_payload_item(lang=normalized_lang) for entry in liquidation_entries]
    gemini_cfg = await get_gemini_config(db) if normalized_lang != "en" else None
    ai_enabled = normalized_lang == "en" or bool(gemini_cfg)

    return {
        "latest": latest,
        "liquidations": liquidations,
        "updatedAt": _utc_now().isoformat(),
        "lang": normalized_lang,
        "aiEnabled": ai_enabled,
        "limit": effective_limit,
    }

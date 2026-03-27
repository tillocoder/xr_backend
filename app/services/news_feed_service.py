from __future__ import annotations

import asyncio
import base64
import hashlib
import html as html_lib
import json
import re
import zlib
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
from html.parser import HTMLParser
from time import mktime
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse
from zoneinfo import ZoneInfo

import feedparser
import httpx
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from sqlalchemy import case, delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.entities import NewsArticle, NewsArticleTranslation
from app.services.gemini_service import GeminiClient, build_gemini_client

try:
    import cloudscraper
except Exception:
    cloudscraper = None


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
CLOUDSCRAPER_BROWSER = {"browser": "chrome", "platform": "windows", "desktop": True}
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
ARTICLE_IMAGE_LIMIT = 8
ARTICLE_CONTENT_BLOCK_LIMIT = 12
ARTICLE_TEXT_BLOCK_LIMIT = 7
ARTICLE_INLINE_IMAGE_LIMIT = 4
ARTICLE_BODY_BLOCK_MAX_CHARS = 420
SOCIAL_ICON_TOKENS = frozenset(
    {
        "facebook",
        "instagram",
        "linkedin",
        "twitter",
        "x",
        "youtube",
        "telegram",
        "whatsapp",
        "discord",
        "reddit",
        "bluesky",
        "tiktok",
        "wechat",
        "line",
        "messenger",
        "pinterest",
    }
)
BEINCRYPTO_LISTING_RESERVED_SLUGS = frozenset(
    {
        "about-us",
        "archive",
        "bonus-hunter",
        "bozorlar",
        "category",
        "contact",
        "convert",
        "editorial-policy",
        "exchanges",
        "faq",
        "fikr",
        "jobs",
        "learn",
        "news",
        "newsletters",
        "our-authors",
        "price",
        "privacy-policy",
        "savdo",
        "tahlil",
        "terms-and-conditions",
        "texnologiya",
        "tradfi",
        "yangiliklar",
    }
)
IGNORED_IMAGE_QUERY_KEYS = {
    "auto",
    "blur",
    "crop",
    "dpr",
    "fit",
    "fm",
    "format",
    "h",
    "height",
    "ixid",
    "ixlib",
    "lossless",
    "q",
    "quality",
    "rect",
    "resize",
    "sat",
    "sharp",
    "trim",
    "w",
    "width",
}


@dataclass(frozen=True)
class FeedProvider:
    source: str
    url: str
    site_key: str | None = None
    display_name: str | None = None
    source_lang: str = "en"
    translation_targets: tuple[str, ...] = ("uz", "ru")
    always_relevant: bool = True
    kind: str = "rss"
    app_lang_scoped: bool = False


RSS_FEEDS: tuple[FeedProvider, ...] = (
    FeedProvider(source="The Daily Hodl", url="https://dailyhodl.com/feed/", site_key="dailyhodl"),
    FeedProvider(
        source="Cointelegraph",
        url="https://cointelegraph.com/rss",
        site_key="cointelegraph",
        translation_targets=("uz",),
    ),
    FeedProvider(
        source="Cointelegraph RU",
        url="https://ru.cointelegraph.com/rss",
        site_key="cointelegraph",
        display_name="Cointelegraph",
        source_lang="ru",
        translation_targets=(),
        app_lang_scoped=True,
    ),
    FeedProvider(source="NewsBTC", url="https://www.newsbtc.com/feed/", site_key="newsbtc"),
    FeedProvider(
        source="NewsBTC Liquidations",
        url="https://www.newsbtc.com/tag/liquidation/feed/",
        site_key="newsbtc",
    ),
    FeedProvider(source="Bitcoin Magazine", url="https://bitcoinmagazine.com/feed", site_key="bitcoinmagazine"),
    FeedProvider(source="CryptoSlate", url="https://cryptoslate.com/feed/", site_key="cryptoslate"),
    FeedProvider(source="CoinDesk", url="https://www.coindesk.com/arc/outboundfeeds/rss/", site_key="coindesk"),
    FeedProvider(source="Decrypt", url="https://decrypt.co/feed", site_key="decrypt"),
    FeedProvider(source="U.Today", url="https://u.today/rss", site_key="utoday"),
    FeedProvider(source="CoinGape", url="https://coingape.com/feed/", site_key="coingape"),
    FeedProvider(
        source="CryptoNews",
        url="https://cryptonews.com/news/feed/",
        site_key="cryptonews",
        translation_targets=("uz",),
    ),
    FeedProvider(
        source="CryptoNews RU",
        url="https://cryptonews.com/ru/feed/",
        site_key="cryptonews",
        display_name="CryptoNews",
        source_lang="ru",
        translation_targets=(),
        app_lang_scoped=True,
    ),
    FeedProvider(
        source="BeInCrypto EN",
        url="https://beincrypto.com/feed/",
        site_key="beincrypto",
        display_name="BeInCrypto",
        source_lang="en",
        translation_targets=(),
        app_lang_scoped=True,
    ),
    FeedProvider(
        source="BeInCrypto RU",
        url="https://ru.beincrypto.com/feed/",
        site_key="beincrypto",
        display_name="BeInCrypto",
        source_lang="ru",
        translation_targets=(),
        app_lang_scoped=True,
    ),
    FeedProvider(
        source="BeInCrypto UZ",
        url="https://uz.beincrypto.com/",
        site_key="beincrypto",
        display_name="BeInCrypto",
        source_lang="uz",
        translation_targets=(),
        kind="html_listing",
        app_lang_scoped=True,
    ),
    FeedProvider(
        source="CryptoPanic",
        url="https://cryptopanic.com/developers/api/posts/",
        site_key="cryptopanic",
        kind="html",
    ),
)
RSS_FEED_BY_SOURCE = {provider.source.lower(): provider for provider in RSS_FEEDS}
NATIVE_APP_LANG_SOURCES: tuple[str, ...] = tuple(
    provider.source
    for provider in RSS_FEEDS
    if provider.app_lang_scoped and str(provider.source_lang or "").strip().lower() in {"ru", "uz"}
)
SOURCE_DOMAIN_LABELS: dict[str, str] = {
    "dailyhodl.com": "The Daily Hodl",
    "cointelegraph.com": "Cointelegraph",
    "coindesk.com": "CoinDesk",
    "newsbtc.com": "NewsBTC",
    "bitcoinmagazine.com": "Bitcoin Magazine",
    "cryptoslate.com": "CryptoSlate",
    "decrypt.co": "Decrypt",
    "u.today": "U.Today",
    "coingape.com": "CoinGape",
    "cryptonews.com": "CryptoNews",
    "beincrypto.com": "BeInCrypto",
    "theblock.co": "The Block",
    "benzinga.com": "Benzinga",
    "cryptopolitan.com": "Cryptopolitan",
    "ethnews.com": "ETHNews",
    "coinpaper.com": "Coinpaper",
    "coindoo.com": "CoinDoo",
    "thestreet.com": "TheStreet",
    "bsc.news": "BSC News",
}
SOURCE_TOKEN_LABELS: dict[str, str] = {
    "ai": "AI",
    "btc": "BTC",
    "dao": "DAO",
    "defi": "DeFi",
    "eth": "ETH",
    "etf": "ETF",
    "nft": "NFT",
    "sec": "SEC",
    "uk": "UK",
    "us": "US",
    "xrp": "XRP",
}

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
    "cascading liquidation",
    "liquidation cascade",
    "long liquidations",
    "short liquidations",
    "rekt",
    "wipeout",
    "wiped out",
    "long squeeze",
    "short squeeze",
    "margin call",
    "leverage",
    "leveraged",
    "forced selling",
]

CATEGORY_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("btc", (" bitcoin", " btc", " spot etf", " blackrock", " microstrategy")),
    ("eth", (" ethereum", " eth", " ether", " staking", " lido")),
    ("altcoins", (" solana", " sol ", " xrp", " altcoin", " ada", " cardano", " doge", " bnb", " ton", " trx")),
    ("macro", (" fed", " inflation", " rates", " cpi", " etf", " sec", " regulation", " congress", " treasury")),
)

INGEST_MIN_INTERVAL_SECONDS = 30 * 60
DAILY_MAX_FETCH_CYCLES = 24
RETENTION_DAYS = 10
TRANSLATE_BACKFILL_MIN_INTERVAL_SECONDS = 30 * 60
MAX_RELEASED_ARTICLES = 100
MAX_STORED_ARTICLES = 420
MAX_UNRELEASED_ARTICLES = MAX_STORED_ARTICLES - MAX_RELEASED_ARTICLES
MAX_NATIVE_APP_LANG_UNRELEASED_ARTICLES = min(120, MAX_UNRELEASED_ARTICLES)
MAX_GENERAL_UNRELEASED_ARTICLES = max(
    0,
    MAX_UNRELEASED_ARTICLES - MAX_NATIVE_APP_LANG_UNRELEASED_ARTICLES,
)
INGEST_MAX_ITEMS_PER_FEED = 10
DUPLICATE_LOOKBACK_HOURS = 48
DUPLICATE_CANDIDATE_LIMIT = 50
RELEASE_SOURCE_ROTATION_LOOKAHEAD = max(32, len(RSS_FEEDS) * 5)
APP_LOCAL_TZ = ZoneInfo("Asia/Tashkent")
RELEASE_WINDOW_START_HOUR = 8
RELEASE_WINDOW_END_HOUR = 20
DAILY_MAX_RELEASED_ARTICLES = 48
DEFAULT_SOURCE_DAILY_CAP = 2
SOURCE_DAILY_CAPS: dict[str, int] = {
    "cryptopanic": 2,
    "dailyhodl": 2,
}
PASSTHROUGH_NEWS_SOURCES: frozenset[str] = frozenset({"beincrypto"})


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
    image_urls: tuple[str, ...] = ()


@dataclass(frozen=True)
class UrlFetchResult:
    text: str
    resolved_url: str
    status_code: int


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
    image_urls: tuple[str, ...]
    published_at: datetime | None
    released_at: datetime | None
    is_liquidation: bool
    category: str
    view_count: int
    translations: dict[str, dict[str, object]]

    def has_lang(self, lang: str) -> bool:
        normalized_lang = _normalize_lang(lang)
        payload = self.translations.get(normalized_lang) or {}
        return _payload_has_localized_text(payload, lang=normalized_lang)

    def is_notification_ready(self) -> bool:
        return all(self.has_lang(lang) for lang in _required_langs_for_source(self.source))

    def to_payload_item(self, *, lang: str) -> dict[str, object]:
        normalized_lang = _normalize_lang(lang)
        source_lang = _source_lang_for_source(self.source)
        source_payload = self.translations.get(source_lang) or {}
        if _should_passthrough_source_to_app(self.source):
            localized = source_payload
        else:
            localized = self.translations.get(normalized_lang)
            if localized is None:
                localized = self.translations.get("en")
            if localized is None:
                localized = source_payload
        if localized is None and self.translations:
            localized = next(iter(self.translations.values()))
        localized = localized or {"title": "", "summary": ""}
        localized_blocks = _normalize_content_blocks(localized.get("contentBlocks"))
        if not localized_blocks and normalized_lang == source_lang:
            localized_blocks = _normalize_content_blocks(source_payload.get("contentBlocks"))
        published_at = self.published_at.isoformat() if self.published_at else ""
        display_source = _resolve_display_source(self.source, self.url)
        return {
            "id": self.article_id,
            "source": display_source,
            "title": str(localized.get("title") or "").strip(),
            "summary": str(localized.get("summary") or "").strip(),
            "image": self.image_url,
            "images": list(self.image_urls),
            "contentBlocks": list(localized_blocks),
            "time": "",
            "publishedAt": published_at,
            "url": self.url,
            "category": self.category,
            "viewCount": self.view_count,
            "readMoreUrl": self.url,
        }

    def to_event_payload(self) -> dict[str, object]:
        published_at = self.published_at.isoformat() if self.published_at else ""
        display_source = _resolve_display_source(self.source, self.url)
        return {
            "id": self.article_id,
            "uid": self.uid,
            "source": display_source,
            "url": self.url,
            "image": self.image_url,
            "images": list(self.image_urls),
            "publishedAt": published_at,
            "isLiquidation": self.is_liquidation,
            "category": self.category,
            "viewCount": self.view_count,
            "translations": {
                lang: {
                    "title": str(payload.get("title") or "").strip(),
                    "summary": str(payload.get("summary") or "").strip(),
                    "contentBlocks": list(_normalize_content_blocks(payload.get("contentBlocks"))),
                }
                for lang, payload in self.translations.items()
            },
        }


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _local_now(now: datetime | None = None) -> datetime:
    current = now or _utc_now()
    return current.astimezone(APP_LOCAL_TZ)


def _today_str(now: datetime | None = None) -> str:
    return _local_now(now).date().isoformat()


def _local_day_bounds(now: datetime) -> tuple[datetime, datetime]:
    local_current = _local_now(now)
    local_start = local_current.replace(hour=0, minute=0, second=0, microsecond=0)
    local_end = local_start + timedelta(days=1)
    return local_start.astimezone(timezone.utc), local_end.astimezone(timezone.utc)


def _release_window_bounds(now: datetime) -> tuple[datetime, datetime, datetime]:
    local_current = _local_now(now)
    local_start = local_current.replace(
        hour=RELEASE_WINDOW_START_HOUR,
        minute=0,
        second=0,
        microsecond=0,
    )
    local_end = local_current.replace(
        hour=RELEASE_WINDOW_END_HOUR,
        minute=0,
        second=0,
        microsecond=0,
    )
    return local_current, local_start, local_end


def _is_release_window_open(now: datetime) -> bool:
    local_current, local_start, local_end = _release_window_bounds(now)
    return local_start <= local_current < local_end


def _allowed_release_count_by_now(now: datetime) -> int:
    local_current, local_start, local_end = _release_window_bounds(now)
    if local_current < local_start or local_current >= local_end:
        return 0
    total_window_seconds = max(1.0, (local_end - local_start).total_seconds())
    slot_seconds = total_window_seconds / float(DAILY_MAX_RELEASED_ARTICLES)
    elapsed_seconds = max(0.0, (local_current - local_start).total_seconds())
    return min(
        DAILY_MAX_RELEASED_ARTICLES,
        int(elapsed_seconds // slot_seconds) + 1,
    )


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


def _payload_has_localized_text(payload: dict[str, object] | None, *, lang: str) -> bool:
    normalized_lang = _normalize_lang(lang)
    candidate = payload or {}
    text = " ".join(
        part.strip()
        for part in (
            str(candidate.get("title") or ""),
            str(candidate.get("summary") or ""),
        )
        if part.strip()
    )
    if not text:
        return False
    if normalized_lang != "en" and _looks_english(text):
        return False
    return True


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


def _site_key_for_source(source: str) -> str:
    provider = _provider_for_source(source)
    if provider is not None and str(provider.site_key or "").strip():
        return str(provider.site_key).strip().lower()
    return (source or "").strip().lower()


def _normalize_source_host(host: str) -> str:
    normalized = (host or "").strip().lower()
    for prefix in ("www.", "m."):
        if normalized.startswith(prefix):
            normalized = normalized[len(prefix) :]
    return normalized


def _format_source_slug(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", " ", (value or "").strip().lower())
    if not normalized:
        return ""
    words: list[str] = []
    for part in normalized.split():
        label = SOURCE_TOKEN_LABELS.get(part)
        if label is not None:
            words.append(label)
        else:
            words.append(part.capitalize())
    return " ".join(words)


def _friendly_source_from_url(url: str) -> str:
    parsed = urlparse((url or "").strip())
    host = _normalize_source_host(parsed.netloc)
    if not host:
        return ""

    if host in {"x.com", "twitter.com"}:
        segments = [segment.strip() for segment in parsed.path.split("/") if segment.strip()]
        if segments:
            username = segments[0].lstrip("@")
            if username and username.lower() not in {"home", "i", "search", "explore", "hashtag", "share"}:
                return f"@{username}"
        return "X"

    label = SOURCE_DOMAIN_LABELS.get(host)
    if label:
        return label

    parts = host.split(".")
    if not parts:
        return ""
    source_key = parts[0]
    if len(parts) >= 2:
        source_key = parts[-2]
    if len(parts) >= 3 and parts[-2] in {"co", "com", "org", "net"}:
        source_key = parts[-3]
    return _format_source_slug(source_key)


def _friendly_source_from_cryptopanic_row(row: dict[str, object], url: str) -> str:
    raw_source = row.get("source")
    if isinstance(raw_source, dict):
        domain_slug = str(raw_source.get("domain_slug") or "").strip()
        if domain_slug.startswith("@"):
            return domain_slug

        title = (
            str(raw_source.get("title") or "")
            .replace("\u200f", "")
            .replace("\u200e", "")
            .strip()
        )
        if title:
            lowered = title.lower()
            for prefix in ("x - ", "twitter - "):
                if lowered.startswith(prefix):
                    handle = title[len(prefix) :].strip().lstrip("@")
                    if handle:
                        return f"@{handle}"
            if lowered != "cryptopanic":
                return title

        domain = str(raw_source.get("domain") or "").strip()
        if domain:
            label = _friendly_source_from_url(f"https://{domain}")
            if label:
                return label

    domain = str(row.get("domain") or "").strip()
    if domain:
        label = _friendly_source_from_url(f"https://{domain}")
        if label:
            return label

    return _friendly_source_from_url(url) or "CryptoPanic"


def _resolve_display_source(source: str, url: str) -> str:
    cleaned = (source or "").strip()
    provider = _provider_for_source(cleaned)
    if provider is not None and str(provider.display_name or "").strip():
        return str(provider.display_name).strip()
    if cleaned and cleaned.lower() != "cryptopanic":
        return cleaned
    return _friendly_source_from_url(url) or cleaned


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


def _is_app_lang_scoped_source(source: str) -> bool:
    provider = _provider_for_source(source)
    return bool(provider is not None and provider.app_lang_scoped)


def _should_passthrough_source_to_app(source: str) -> bool:
    return _site_key_for_source(source) in PASSTHROUGH_NEWS_SOURCES


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


def _sanitize_article_block_text(value: str) -> str:
    normalized = _sanitize_preview_text(value)
    if not normalized:
        return ""
    return _clip_preview_text(normalized, max_chars=ARTICLE_BODY_BLOCK_MAX_CHARS)


def _normalize_content_blocks(
    *groups: object,
    limit: int = ARTICLE_CONTENT_BLOCK_LIMIT,
) -> tuple[dict[str, str], ...]:
    blocks: list[dict[str, str]] = []
    seen_text: set[str] = set()
    seen_images: set[str] = set()

    def add_block(raw_block: object) -> None:
        if not isinstance(raw_block, dict):
            return
        block_type = str(raw_block.get("type") or "").strip().lower()
        if block_type == "image":
            image_url = _canonicalize_image_url(
                str(raw_block.get("url") or "").strip(),
                base_url=str(raw_block.get("base_url") or "").strip(),
            )
            image_key = _image_dedupe_key(image_url)
            if not image_url or not image_key or image_key in seen_images:
                return
            seen_images.add(image_key)
            payload = {"type": "image", "url": image_url}
            caption = _sanitize_article_block_text(str(raw_block.get("caption") or "").strip())
            if caption and len(caption) >= 16 and not _looks_like_market_strip(caption):
                payload["caption"] = caption
            blocks.append(payload)
            return

        text = _sanitize_article_block_text(
            str(raw_block.get("text") or raw_block.get("value") or "").strip()
        )
        if len(text) < 24:
            return
        key = _normalize_text(text)
        if not key or key in seen_text:
            return
        seen_text.add(key)
        blocks.append({"type": "text", "text": text})

    for group in groups:
        if isinstance(group, dict):
            add_block(group)
            continue
        if isinstance(group, (list, tuple, set)):
            for raw_block in group:
                add_block(raw_block)
            continue

    if limit > 0:
        blocks = blocks[:limit]
    return tuple(blocks)


def _summary_to_content_blocks(summary: str, title: str) -> tuple[dict[str, str], ...]:
    base = _article_summary(summary, title)
    normalized = _sanitize_article_block_text(base)
    if not normalized:
        fallback = _sanitize_article_block_text(title)
        if not fallback:
            return ()
        return ({"type": "text", "text": fallback},)

    sentences = [
        sentence.strip()
        for sentence in re.split(r"(?<=[\.\!\?])\s+", normalized)
        if sentence.strip()
    ]
    if not sentences:
        return ({"type": "text", "text": normalized},)

    blocks: list[dict[str, str]] = []
    current: list[str] = []
    current_length = 0
    for sentence in sentences:
        extra = len(sentence) + (1 if current else 0)
        if current and (current_length + extra > 220 or len(current) >= 2):
            blocks.append({"type": "text", "text": " ".join(current).strip()})
            current = []
            current_length = 0
        current.append(sentence)
        current_length += extra
    if current:
        blocks.append({"type": "text", "text": " ".join(current).strip()})

    return _normalize_content_blocks(blocks, limit=3)


def _summary_from_content_blocks(
    content_blocks: tuple[dict[str, str], ...],
    *,
    fallback: str,
) -> str:
    text_parts = [
        str(block.get("text") or "").strip()
        for block in content_blocks
        if str(block.get("type") or "").strip().lower() == "text"
        and str(block.get("text") or "").strip()
    ]
    if not text_parts:
        return _clip_preview_text(fallback, max_chars=ARTICLE_SOURCE_MAX_CHARS)
    return _clip_preview_text(" ".join(text_parts[:3]), max_chars=ARTICLE_SOURCE_MAX_CHARS)


class _ArticleContentBlockParser(HTMLParser):
    _SKIP_TAGS = {"script", "style", "noscript", "svg", "footer", "header", "nav", "aside", "form"}
    _TEXT_TAGS = {"p", "h2", "h3", "h4", "li", "blockquote"}
    _IMAGE_ATTRS = ("src", "data-src", "data-original", "data-lazy-src", "data-image")

    def __init__(self, *, base_url: str = "") -> None:
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.blocks: list[dict[str, str]] = []
        self._skip_depth = 0
        self._active_text_tag: str | None = None
        self._text_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        name = (tag or "").strip().lower()
        if name in self._SKIP_TAGS:
            self._skip_depth += 1
            return
        if self._skip_depth:
            return
        if name in self._TEXT_TAGS:
            self._flush_text()
            self._active_text_tag = name
            self._text_parts = []
            return
        if name == "br" and self._active_text_tag is not None:
            self._text_parts.append("\n")
            return
        if name != "img":
            return

        self._flush_text()
        attr_map = {str(key or "").strip().lower(): str(value or "").strip() for key, value in attrs}
        image_url = ""
        for attr_name in self._IMAGE_ATTRS:
            candidate = attr_map.get(attr_name) or ""
            if candidate:
                image_url = candidate
                break
        if not image_url:
            srcset = attr_map.get("srcset") or ""
            for chunk in srcset.split(","):
                first = chunk.strip().split(" ", 1)[0].strip()
                if first:
                    image_url = first
                    break
        if not image_url:
            return
        payload = {
            "type": "image",
            "url": image_url,
            "base_url": self.base_url,
        }
        alt = _sanitize_article_block_text(attr_map.get("alt") or "")
        if alt and len(alt) >= 16 and not _looks_like_market_strip(alt):
            payload["caption"] = alt
        self.blocks.append(payload)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)

    def handle_data(self, data: str) -> None:
        if self._skip_depth or self._active_text_tag is None:
            return
        self._text_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        name = (tag or "").strip().lower()
        if name in self._SKIP_TAGS:
            if self._skip_depth > 0:
                self._skip_depth -= 1
            return
        if self._skip_depth:
            return
        if self._active_text_tag == name:
            self._flush_text()

    def close(self) -> None:
        self._flush_text()
        super().close()

    def _flush_text(self) -> None:
        if self._active_text_tag is None:
            return
        text = _sanitize_article_block_text(" ".join(self._text_parts))
        if text:
            self.blocks.append({"type": "text", "text": text})
        self._active_text_tag = None
        self._text_parts = []


def _select_article_content_blocks(
    raw_blocks: list[dict[str, str]],
    *,
    title: str = "",
) -> tuple[dict[str, str], ...]:
    selected: list[dict[str, str]] = []
    seen_text: set[str] = set()
    seen_images: set[str] = set()
    started = False
    text_count = 0
    image_count = 0
    total_text_chars = 0

    for raw_block in raw_blocks:
        normalized_group = _normalize_content_blocks(raw_block, limit=1)
        if not normalized_group:
            continue
        block = dict(normalized_group[0])
        block_type = str(block.get("type") or "").strip().lower()
        if block_type == "text":
            text = str(block.get("text") or "").strip()
            if not text or _is_low_signal_paragraph(text, title=title):
                continue
            key = _normalize_text(text)
            if not key or key in seen_text:
                continue
            seen_text.add(key)
            started = True
            selected.append({"type": "text", "text": text})
            text_count += 1
            total_text_chars += len(text)
        elif block_type == "image":
            image_url = str(block.get("url") or "").strip()
            image_key = _image_dedupe_key(image_url)
            if not image_url or not started or image_count >= ARTICLE_INLINE_IMAGE_LIMIT:
                continue
            if not image_key or image_key in seen_images:
                continue
            seen_images.add(image_key)
            payload = {"type": "image", "url": image_url}
            caption = str(block.get("caption") or "").strip()
            if caption:
                payload["caption"] = caption
            selected.append(payload)
            image_count += 1

        if len(selected) >= ARTICLE_CONTENT_BLOCK_LIMIT:
            break
        if text_count >= ARTICLE_TEXT_BLOCK_LIMIT and total_text_chars >= int(ARTICLE_SOURCE_MAX_CHARS * 0.7):
            break

    return tuple(selected)


def _extract_article_page_content_blocks(
    html: str,
    *,
    title: str = "",
    base_url: str = "",
) -> tuple[dict[str, str], ...]:
    if not html:
        return ()
    parser = _ArticleContentBlockParser(base_url=base_url)
    try:
        parser.feed(html)
        parser.close()
    except Exception:
        return ()
    return _select_article_content_blocks(parser.blocks, title=title)


def _looks_like_market_strip(value: str) -> bool:
    normalized = re.sub(r"\s+", " ", str(value or "")).strip()
    if not normalized:
        return False
    percent_count = normalized.count("%")
    dollar_count = normalized.count("$")
    ticker_count = len(re.findall(r"\b[A-Z]{2,6}\b", normalized))
    return (percent_count >= 3 and ticker_count >= 4) or (dollar_count >= 2 and ticker_count >= 4)


def _important_word_tokens(value: str) -> set[str]:
    return {
        token
        for token in re.findall(r"[a-zA-ZÀ-ÿ\u0400-\u04FF\u0600-\u06FF']+", str(value or "").lower())
        if len(token) >= 4
    }


def _looks_like_keyword_dump(value: str, *, title: str = "") -> bool:
    normalized = re.sub(r"\s+", " ", str(value or "")).strip()
    if len(normalized) < 50:
        return False
    comma_segments = [segment.strip() for segment in normalized.split(",") if segment.strip()]
    sentence_count = max(
        1,
        len([part for part in re.split(r"(?<=[\.\!\?])\s+", normalized) if part.strip()]),
    )
    if sentence_count > 3:
        return False
    if title:
        title_tokens = _important_word_tokens(title)
        if title_tokens:
            text_tokens = _important_word_tokens(normalized)
            overlap = len(title_tokens & text_tokens)
            if overlap >= 2:
                return False
    if len(normalized) >= 90 and len(comma_segments) >= 6:
        short_segments = sum(
            1
            for segment in comma_segments
            if len(re.findall(r"[a-zA-ZÀ-ÿ\u0400-\u04FF\u0600-\u06FF']+", segment)) <= 5
        )
        if short_segments >= max(5, int(len(comma_segments) * 0.65)):
            return True
    word_tokens = re.findall(r"[A-Za-zÀ-ÿ\u0400-\u04FF']+", normalized)
    title_like_tokens = [
        token
        for token in re.findall(r"\b[A-Z][A-Za-z&/-]{2,}\b|\b[A-Z]{2,6}\b", normalized)
        if len(token) >= 3
    ]
    punctuation_count = len(re.findall(r"[,\.\!\?;:]", normalized))
    if (
        len(word_tokens) >= 7
        and punctuation_count <= 3
        and len(title_like_tokens) >= max(5, int(len(word_tokens) * 0.5))
    ):
        return True
    return False


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
    if _looks_like_keyword_dump(normalized, title=title):
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


def _extract_html_attributes(tag: str) -> dict[str, str]:
    attrs: dict[str, str] = {}
    for match in re.finditer(r'([a-zA-Z_:.-]+)\s*=\s*(["\'])(.*?)\2', tag, flags=re.S):
        attrs[str(match.group(1) or "").strip().lower()] = html_lib.unescape(
            str(match.group(3) or "").strip()
        )
    return attrs


def _extract_meta_tag_content(html: str, *keys: str) -> str:
    if not html or not keys:
        return ""
    wanted = {str(key or "").strip().lower() for key in keys if str(key or "").strip()}
    if not wanted:
        return ""
    for match in re.finditer(r"<meta\b[^>]*>", html, flags=re.I | re.S):
        attrs = _extract_html_attributes(match.group(0) or "")
        content = str(attrs.get("content") or "").strip()
        if not content:
            continue
        for attr_name in ("property", "name", "itemprop"):
            attr_value = str(attrs.get(attr_name) or "").strip().lower()
            if attr_value in wanted:
                return content
    return ""


def _clean_article_page_title(value: str) -> str:
    cleaned = _sanitize_preview_text(html_lib.unescape(value or ""))
    if not cleaned:
        return ""
    cleaned = re.sub(r"\s*[-|–]\s*BeInCrypto(?:\s+[A-Za-z]+)?\s*$", "", cleaned, flags=re.I)
    return cleaned.strip()


def _extract_article_page_title(html: str, *, fallback: str = "") -> str:
    for candidate in (
        _extract_meta_tag_content(html, "og:title", "twitter:title"),
        re.search(r"<h1[^>]*>(.*?)</h1>", html, flags=re.I | re.S),
        re.search(r"<title>(.*?)</title>", html, flags=re.I | re.S),
        fallback,
    ):
        if hasattr(candidate, "group"):
            raw = str(candidate.group(1) or "").strip()
        else:
            raw = str(candidate or "").strip()
        cleaned = _clean_article_page_title(raw)
        if cleaned:
            return cleaned
    return ""


def _extract_article_page_published_at(html: str) -> datetime | None:
    for raw_value in (
        _extract_meta_tag_content(
            html,
            "article:published_time",
            "og:published_time",
            "datepublished",
            "datecreated",
        ),
        next(
            (
                str(match.group(1) or "").strip()
                for match in [
                    re.search(r'<time[^>]+datetime=["\']([^"\']+)["\']', html, flags=re.I | re.S),
                    re.search(r'"datePublished"\s*:\s*"([^"]+)"', html, flags=re.I | re.S),
                ]
                if match
            ),
            "",
        ),
    ):
        parsed = _parse_iso_datetime(raw_value)
        if parsed is not None:
            return parsed
    return None


@dataclass(frozen=True)
class ArticlePageExtract:
    summary: str
    image_urls: tuple[str, ...] = ()
    content_blocks: tuple[dict[str, str], ...] = ()


def _extract_article_page_enrichment(html: str, *, title: str = "", base_url: str = "") -> ArticlePageExtract:
    content_blocks = _extract_article_page_content_blocks(html, title=title, base_url=base_url)
    summary = _extract_article_page_preview_text(html, title=title)
    if not summary:
        summary = _sanitize_preview_text(
            _extract_meta_tag_content(html, "description", "og:description", "twitter:description")
        )
    return ArticlePageExtract(
        summary=summary,
        image_urls=_normalize_image_urls(
            [block.get("url") for block in content_blocks if block.get("type") == "image"],
            _extract_image_urls_from_html(html, base_url=base_url),
        ),
        content_blocks=content_blocks,
    )


def _uses_cloudscraper(url: str) -> bool:
    host = _normalize_source_host(urlparse(str(url or "").strip()).netloc)
    return host.endswith("beincrypto.com")


def _cloudscraper_headers(url: str) -> dict[str, str]:
    headers = dict(DEFAULT_HTTP_HEADERS)
    host = _normalize_source_host(urlparse(str(url or "").strip()).netloc)
    if host.startswith("uz."):
        headers["Accept-Language"] = "uz-UZ,uz;q=0.9,en;q=0.8"
    elif host.startswith("ru."):
        headers["Accept-Language"] = "ru-RU,ru;q=0.9,en;q=0.8"
    return headers


def _cloudscraper_fetch_sync(url: str) -> UrlFetchResult:
    normalized_url = _canonicalize_url(url)
    if not normalized_url or cloudscraper is None:
        return UrlFetchResult(text="", resolved_url=normalized_url, status_code=0)
    try:
        scraper = cloudscraper.create_scraper(browser=CLOUDSCRAPER_BROWSER)
        response = scraper.get(
            normalized_url,
            headers=_cloudscraper_headers(normalized_url),
            timeout=20,
        )
    except Exception:
        return UrlFetchResult(text="", resolved_url=normalized_url, status_code=0)
    return UrlFetchResult(
        text=(response.text or "").strip(),
        resolved_url=str(response.url or normalized_url),
        status_code=int(response.status_code or 0),
    )


async def _fetch_url_result(url: str) -> UrlFetchResult:
    normalized_url = _canonicalize_url(url)
    if not normalized_url:
        return UrlFetchResult(text="", resolved_url="", status_code=0)
    if _uses_cloudscraper(normalized_url):
        try:
            return await asyncio.to_thread(_cloudscraper_fetch_sync, normalized_url)
        except Exception:
            return UrlFetchResult(text="", resolved_url=normalized_url, status_code=0)

    timeout = httpx.Timeout(connect=8, read=18, write=10, pool=10)
    limits = httpx.Limits(max_connections=8, max_keepalive_connections=4)
    try:
        async with httpx.AsyncClient(
            timeout=timeout,
            limits=limits,
            headers=DEFAULT_HTTP_HEADERS,
            follow_redirects=True,
        ) as client:
            response = await client.get(normalized_url)
    except Exception:
        return UrlFetchResult(text="", resolved_url=normalized_url, status_code=0)
    return UrlFetchResult(
        text=(response.text or "").strip(),
        resolved_url=str(response.url or normalized_url),
        status_code=int(response.status_code or 0),
    )


async def _fetch_article_page_enrichment(url: str, *, title: str = "") -> ArticlePageExtract:
    normalized_url = _canonicalize_url(url)
    if not normalized_url:
        return ArticlePageExtract(summary="", image_urls=(), content_blocks=())
    response = await _fetch_url_result(normalized_url)
    if response.status_code >= 400 or not response.text:
        return ArticlePageExtract(summary="", image_urls=(), content_blocks=())
    return _extract_article_page_enrichment(
        response.text,
        title=title,
        base_url=response.resolved_url,
    )


async def _build_article_enrichment(
    *,
    summary: str,
    title: str,
    url: str,
    current_image_urls: tuple[str, ...] = (),
    current_content_blocks: tuple[dict[str, str], ...] = (),
) -> ArticlePageExtract:
    base_summary = _article_summary(summary, title)
    normalized_images = _normalize_image_urls(current_image_urls)
    normalized_blocks = _normalize_content_blocks(current_content_blocks)
    fallback_blocks = normalized_blocks or _summary_to_content_blocks(base_summary, title)
    needs_page_fetch = (
        not _summary_has_enough_detail(base_summary)
        or len(normalized_images) < 2
        or not normalized_blocks
    )
    if not needs_page_fetch:
        return ArticlePageExtract(
            summary=base_summary,
            image_urls=normalized_images,
            content_blocks=fallback_blocks,
        )

    page_extract = await _fetch_article_page_enrichment(url, title=title)
    page_summary = page_extract.summary
    if not page_summary or (
        len(page_summary) <= len(base_summary)
        and _sentence_count(page_summary) <= _sentence_count(base_summary)
    ):
        page_summary = base_summary

    page_blocks = _normalize_content_blocks(page_extract.content_blocks)
    if not page_blocks:
        page_blocks = fallback_blocks

    return ArticlePageExtract(
        summary=page_summary,
        image_urls=_normalize_image_urls(normalized_images, page_extract.image_urls),
        content_blocks=page_blocks,
    )


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


def _canonicalize_image_url(url: str, *, base_url: str = "") -> str:
    raw = str(url or "").strip()
    if not raw:
        return ""
    raw = html_lib.unescape(raw)
    raw = re.sub(r"&amp(?:%3[Bb]|;)?", "&", raw)
    raw = re.sub(r"([&?])%3[Bb]", r"\1", raw)
    raw = raw.replace("&#038;", "&")
    if raw.startswith(("data:", "blob:", "javascript:")):
        return ""
    resolved = urljoin(base_url, raw) if base_url else raw
    normalized = _canonicalize_url(resolved)
    if not normalized:
        return ""
    parsed = urlparse(normalized)
    if parsed.path.lower().endswith("/_next/image"):
        wrapped_url = dict(parse_qsl(parsed.query, keep_blank_values=True)).get("url", "").strip()
        if wrapped_url:
            if wrapped_url.startswith("/"):
                wrapped_url = urljoin(f"{parsed.scheme}://{parsed.netloc}", wrapped_url)
            return _canonicalize_image_url(wrapped_url, base_url=base_url)
    if parsed.scheme not in {"http", "https"}:
        return ""
    if not parsed.netloc.strip():
        return ""
    if not _is_probable_article_image_url(normalized):
        return ""
    return normalized


def _normalize_image_path_for_dedupe(path: str, *, host: str) -> str:
    normalized = (path or "").strip()
    if not normalized:
        return normalized

    normalized = re.sub(r"-(\d{2,5})x(\d{2,5})(?=\.[a-z0-9]+$)", "", normalized, flags=re.I)
    normalized = re.sub(r"\.(?:jpe?g|png|webp|gif|avif)(?=$)", "", normalized, flags=re.I)

    if "res.cloudinary.com" in host or "res.coinpaper.com" in host:
        marker = "/image/upload/"
        marker_index = normalized.find(marker)
        if marker_index >= 0:
            base_prefix = normalized[:marker_index]
            base_parts = [part for part in base_prefix.split("/") if part]
            remainder = normalized[marker_index + len(marker) :]
            parts = [part for part in remainder.split("/") if part]
            if len(parts) > 1:
                while len(parts) > 1 and (
                    _looks_like_transform_segment(parts[0]) or _looks_like_version_segment(parts[0])
                ):
                    parts = parts[1:]
                normalized = "/" + "/".join([*base_parts, *parts])

    if "res.coinpaper.com" in host:
        parts = [part for part in normalized.split("/") if part]
        if len(parts) >= 3:
            prefix = [parts[0]]
            remainder = parts[1:]
            while len(remainder) > 1 and (
                _looks_like_transform_segment(remainder[0]) or _looks_like_version_segment(remainder[0])
            ):
                remainder = remainder[1:]
            normalized = "/" + "/".join(prefix + remainder)

    return normalized


def _looks_like_transform_segment(segment: str) -> bool:
    normalized = (segment or "").strip().lower()
    if not normalized:
        return False
    token_pattern = re.compile(
        r"(?:c|dpr|f|fit|fm|format|h|height|q|quality|rect|resize|trim|w|width)_[a-z0-9:_-]+"
    )
    if token_pattern.fullmatch(normalized):
        return True
    if "," not in normalized:
        return False
    parts = [part.strip() for part in normalized.split(",") if part.strip()]
    return bool(parts) and all(token_pattern.fullmatch(part) for part in parts)


def _looks_like_version_segment(segment: str) -> bool:
    normalized = (segment or "").strip().lower()
    return bool(re.fullmatch(r"v\d+", normalized))


def _is_probable_article_image_url(url: str) -> bool:
    parsed = urlparse(str(url or "").strip())
    host = parsed.netloc.strip().lower()
    path = parsed.path.strip().lower()
    query = parsed.query.strip().lower()
    if not host or not path or path == "/":
        return False
    if any(marker in path for marker in ("/_next/static/", "/static/media/")):
        return False
    if any(marker in path for marker in ("/favicon", "/apple-touch-icon", "/android-chrome")):
        return False
    if "/brandicons/" in path or "/social/" in path or "/share/" in path:
        return False
    if "google-news" in path:
        return False

    parts = [part for part in parsed.path.split("/") if part]
    if not parts:
        return False
    filename = parts[-1].strip().lower()
    stem = re.sub(r"\.(?:jpe?g|png|webp|gif|avif|svg)$", "", filename, flags=re.I)
    if stem in SOCIAL_ICON_TOKENS:
        return False
    if host.endswith("benzinga.com") and (
        "bz-icon" in stem
        or "article-header-background-image" in stem
        or ("/themes/" in path and stem.endswith("-icon"))
    ):
        return False
    if host.startswith("image-util.benzinga.com") and "/api/v2/logos/file/image/" in path:
        return False
    if "mark_vector" in path or "security_symbol=" in query:
        return False

    is_image_cdn = any(token in host for token in ("cloudinary.com", "res.coinpaper.com", "cdn.sanity.io"))
    if not is_image_cdn and len(parts) > 1 and any(
        _looks_like_transform_segment(part) for part in parts[:-1]
    ):
        return False

    if _looks_like_transform_segment(parts[-1]):
        return False
    return True


def _image_dedupe_key(url: str) -> str:
    normalized = str(url or "").strip()
    if not normalized:
        return ""

    parsed = urlparse(normalized)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc.strip():
        return normalized.lower()

    filtered_query: list[tuple[str, str]] = []
    for key, value in parse_qsl(parsed.query, keep_blank_values=True):
        key_norm = key.strip().lower()
        if not key_norm:
            continue
        if key_norm.startswith("utm_") or key_norm in IGNORED_IMAGE_QUERY_KEYS:
            continue
        filtered_query.append((key_norm, value.strip()))

    filtered_query.sort(key=lambda item: item[0])
    canonical = parsed._replace(
        scheme=parsed.scheme.lower(),
        netloc=parsed.netloc.lower(),
        path=_normalize_image_path_for_dedupe(parsed.path, host=parsed.netloc.lower()),
        query=urlencode(filtered_query, doseq=True),
        fragment="",
    )
    return urlunparse(canonical)


def _normalize_image_urls(*groups: object, limit: int = ARTICLE_IMAGE_LIMIT) -> tuple[str, ...]:
    urls: list[str] = []
    seen: set[str] = set()

    def add_candidate(value: object, *, base_url: str = "") -> None:
        normalized = _canonicalize_image_url(str(value or "").strip(), base_url=base_url)
        key = _image_dedupe_key(normalized)
        if not normalized or not key or key in seen:
            return
        seen.add(key)
        urls.append(normalized)

    for group in groups:
        if isinstance(group, dict):
            add_candidate(group.get("url"), base_url=str(group.get("base_url") or ""))
            continue
        if isinstance(group, (list, tuple, set)):
            for value in group:
                if isinstance(value, dict):
                    add_candidate(value.get("url"), base_url=str(value.get("base_url") or ""))
                else:
                    add_candidate(value)
            continue
        add_candidate(group)

    if limit > 0:
        urls = urls[:limit]
    return tuple(urls)


def _extract_image_urls_from_html(html: str, *, base_url: str = "") -> tuple[str, ...]:
    if not html:
        return ()

    html = html_lib.unescape(html)
    candidates: list[dict[str, str]] = []

    for match in re.finditer(
        r'<meta[^>]+(?:property|name)=["\'](?:og:image|twitter:image)["\'][^>]+content=["\']([^"\']+)["\']',
        html,
        flags=re.I,
    ):
        candidates.append({"url": (match.group(1) or "").strip(), "base_url": base_url})

    for tag_match in re.finditer(r"<img\b[^>]*>", html, flags=re.I):
        tag = tag_match.group(0) or ""
        for attr in ("src", "data-src", "data-original", "data-lazy-src", "data-image"):
            attr_match = re.search(rf'{attr}=["\']([^"\']+)["\']', tag, flags=re.I)
            if attr_match:
                candidates.append({"url": (attr_match.group(1) or "").strip(), "base_url": base_url})
                break
        srcset_match = re.search(r'srcset=["\']([^"\']+)["\']', tag, flags=re.I)
        if srcset_match:
            raw = (srcset_match.group(1) or "").strip()
            for first in _extract_srcset_candidates(raw):
                candidates.append({"url": first, "base_url": base_url})

    return _normalize_image_urls(candidates)


def _extract_srcset_candidates(value: str) -> tuple[str, ...]:
    raw = str(value or "").strip()
    if not raw:
        return ()

    out: list[str] = []
    length = len(raw)
    index = 0
    while index < length:
        while index < length and raw[index] in {" ", "\t", "\n", "\r", ","}:
            index += 1
        if index >= length:
            break

        start = index
        while index < length and not raw[index].isspace():
            index += 1
        candidate = raw[start:index].strip().rstrip(",")
        if candidate:
            out.append(candidate)

        while index < length and raw[index] != ",":
            index += 1
        if index < length and raw[index] == ",":
            index += 1

    return tuple(out)


def _first_image_url(image_urls: tuple[str, ...]) -> str:
    return image_urls[0] if image_urls else ""


def _coerce_article_image_urls(article: NewsArticle) -> tuple[str, ...]:
    stored = article.images_json if isinstance(article.images_json, list) else []
    return _normalize_image_urls(article.image_url or "", stored)


def _coerce_article_content_blocks(article: NewsArticle) -> tuple[dict[str, str], ...]:
    stored = article.content_blocks_json if isinstance(article.content_blocks_json, list) else []
    return _normalize_content_blocks(stored)


def _extract_feed_guid(entry) -> str:
    raw = (
        getattr(entry, "id", None)
        or getattr(entry, "guid", None)
        or getattr(entry, "guidislink", None)
        or getattr(entry, "link", None)
        or ""
    )
    return str(raw or "").strip()


def _collect_entry_images(entry) -> tuple[str, ...]:
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
            candidates.extend(_extract_image_urls_from_html(html))

    summary_raw = (getattr(entry, "summary", "") or "").strip()
    candidates.extend(_extract_image_urls_from_html(summary_raw))

    return _normalize_image_urls(candidates)


def _extract_first_image_from_html(html: str) -> str:
    return _first_image_url(_extract_image_urls_from_html(html))


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
    response = await _fetch_url_result(url)
    if response.status_code >= 400:
        return ""
    return response.text


def _extract_beincrypto_article_urls(html: str, *, base_url: str) -> list[str]:
    article_urls: list[str] = []
    seen: set[str] = set()
    base_host = _normalize_source_host(urlparse(str(base_url or "").strip()).netloc)

    for match in re.finditer(r'<a[^>]+href=(["\'])(.*?)\1', html, flags=re.I | re.S):
        href = html_lib.unescape(str(match.group(2) or "").strip())
        if not href:
            continue
        full_url = _canonicalize_url(urljoin(base_url, href))
        if not full_url:
            continue
        parsed = urlparse(full_url)
        host = _normalize_source_host(parsed.netloc)
        if host != base_host:
            continue
        segments = [segment.strip() for segment in parsed.path.split("/") if segment.strip()]
        if len(segments) != 1:
            continue
        slug = segments[0].strip().lower()
        if not slug or slug in BEINCRYPTO_LISTING_RESERVED_SLUGS:
            continue
        if "." in slug:
            continue
        if full_url in seen:
            continue
        seen.add(full_url)
        article_urls.append(full_url)

    return article_urls


def _beincrypto_listing_urls(provider: FeedProvider) -> tuple[str, ...]:
    base_url = _canonicalize_url(provider.url)
    if not base_url:
        return ()
    source_lang = _normalize_lang(provider.source_lang)
    if source_lang != "uz":
        return (base_url,)
    return (
        base_url,
        urljoin(base_url, "/type/yangiliklar/"),
        urljoin(base_url, "/category/bozorlar/"),
        urljoin(base_url, "/type/tahlil/"),
    )


async def _fetch_beincrypto_listing_items(
    provider: FeedProvider,
    *,
    max_each: int,
) -> list[FeedItem]:
    target_limit = max(1, int(max_each))
    candidate_urls: list[str] = []
    seen_urls: set[str] = set()

    for listing_url in _beincrypto_listing_urls(provider):
        response = await _fetch_url_result(listing_url)
        if response.status_code >= 400 or not response.text:
            continue
        for article_url in _extract_beincrypto_article_urls(response.text, base_url=response.resolved_url):
            if article_url in seen_urls:
                continue
            seen_urls.add(article_url)
            candidate_urls.append(article_url)
            if len(candidate_urls) >= target_limit * 6:
                break
        if len(candidate_urls) >= target_limit * 6:
            break

    items: list[FeedItem] = []
    for article_url in candidate_urls:
        response = await _fetch_url_result(article_url)
        if response.status_code >= 400 or not response.text:
            continue
        title = _extract_article_page_title(response.text)
        if not title:
            continue
        normalized_title = _normalize_text(title)
        if any(token in normalized_title for token in (" archive", " arxiv")):
            continue
        summary = _extract_article_page_preview_text(response.text, title=title)
        if not summary:
            summary = _sanitize_preview_text(
                _extract_meta_tag_content(
                    response.text,
                    "description",
                    "og:description",
                    "twitter:description",
                )
            )
        text = f"{title} {summary}".strip()
        image_urls = _normalize_image_urls(
            _extract_image_urls_from_html(response.text, base_url=response.resolved_url)
        )
        is_liquidation = _matches_keywords(text, LIQ_KEYWORDS)
        items.append(
            FeedItem(
                source=provider.source,
                title=title,
                summary=summary,
                url=article_url,
                image_url=_first_image_url(image_urls),
                published_at=_extract_article_page_published_at(response.text),
                is_liquidation=is_liquidation,
                guid=article_url,
                category=_detect_category(text, is_liquidation=is_liquidation),
                image_urls=image_urls,
            )
        )
        if len(items) >= target_limit:
            break

    return items


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
        image_urls = _collect_entry_images(entry)
        items.append(
            FeedItem(
                source=provider.source,
                title=title,
                summary=summary,
                url=link,
                image_url=_first_image_url(image_urls),
                published_at=_entry_published_at(entry),
                is_liquidation=is_liquidation,
                guid=_extract_feed_guid(entry) or link,
                category=_detect_category(text, is_liquidation=is_liquidation),
                image_urls=image_urls,
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

        row_images = _normalize_image_urls(
            str(row.get("image") or "").strip(),
            _extract_image_urls_from_html(str(row.get("body") or "").strip(), base_url=link),
            _extract_image_urls_from_html(str((row.get("content") or {}).get("clean_v2") or "").strip(), base_url=link)
            if isinstance(row.get("content"), dict)
            else (),
        )
        items.append(
            FeedItem(
                source=_friendly_source_from_cryptopanic_row(row, link),
                title=title,
                summary=summary,
                url=link,
                image_url=_first_image_url(row_images),
                published_at=_parse_iso_datetime(row.get("published_at")),
                is_liquidation=is_liquidation,
                guid=guid or link,
                category=_detect_category(text, is_liquidation=is_liquidation),
                image_urls=row_images,
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
        elif provider.kind == "html_listing":
            items = await _fetch_beincrypto_listing_items(provider, max_each=max_each)
        else:
            items = await _fetch_rss_provider_items(provider, max_each=max_each)
        results.extend(items)

    concurrent_providers: list[FeedProvider] = []
    serial_providers: list[FeedProvider] = []
    for provider in RSS_FEEDS:
        if _uses_cloudscraper(provider.url) or provider.kind == "html_listing":
            serial_providers.append(provider)
        else:
            concurrent_providers.append(provider)

    await _gather_safely([fetch_one(provider) for provider in concurrent_providers])
    for provider in serial_providers:
        await fetch_one(provider)

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
    image_urls = _coerce_article_image_urls(article)
    return FeedItem(
        source=article.source,
        title=article.raw_title,
        summary=_article_summary(article.raw_summary, article.raw_title),
        url=article.url,
        image_url=_first_image_url(image_urls),
        published_at=article.published_at,
        is_liquidation=article.is_liquidation,
        guid=(article.source_guid or article.url),
        category=article.category,
        image_urls=image_urls,
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
) -> dict[str, dict[str, object]]:
    raw_title = (article.raw_title or "").strip()
    raw_summary = _article_summary(article.raw_summary, article.raw_title)
    raw_content_blocks = list(_coerce_article_content_blocks(article))
    source_lang = _source_lang_for_source(article.source)
    translations: dict[str, dict[str, object]] = {}
    if raw_title or raw_summary:
        translations[source_lang] = {
            "title": raw_title,
            "summary": raw_summary,
            "contentBlocks": raw_content_blocks,
        }
    for row in translation_rows:
        lang = _normalize_lang(row.lang)
        title = (row.title or "").strip() or raw_title
        summary = (row.summary or "").strip() or raw_summary
        content_blocks = _normalize_content_blocks(row.content_blocks_json)
        if title or summary:
            translations[lang] = {
                "title": title,
                "summary": summary,
                "contentBlocks": list(content_blocks),
            }
    if "en" not in translations and source_lang == "en":
        translations["en"] = {
            "title": raw_title,
            "summary": raw_summary,
            "contentBlocks": raw_content_blocks,
        }
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

    if _is_app_lang_scoped_source(item.source) and _source_lang_for_source(item.source) in {"ru", "uz"}:
        return False

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


async def _trim_stored_articles(db: AsyncSession, *, keep: int) -> int:
    keep_total = max(1, int(keep))
    keep_released = max(1, min(MAX_RELEASED_ARTICLES, keep_total))
    keep_unreleased = max(0, min(MAX_UNRELEASED_ARTICLES, keep_total - keep_released))
    stale_ids: set[int] = set()
    native_priority = case(
        (NewsArticle.source.in_(NATIVE_APP_LANG_SOURCES), 0),
        else_=1,
    )

    released_ids = (
        await db.scalars(
            select(NewsArticle.id)
            .where(NewsArticle.released_at.is_not(None))
            .order_by(NewsArticle.released_at.desc().nullslast(), NewsArticle.id.desc())
            .offset(keep_released)
        )
    ).all()
    stale_ids.update(int(article_id) for article_id in released_ids)

    native_keep = min(MAX_NATIVE_APP_LANG_UNRELEASED_ARTICLES, keep_unreleased)
    general_keep = min(MAX_GENERAL_UNRELEASED_ARTICLES, keep_unreleased)
    if native_keep + general_keep > keep_unreleased:
        general_keep = max(0, keep_unreleased - native_keep)

    native_unreleased_ids = (
        await db.scalars(
            select(NewsArticle.id)
            .where(NewsArticle.released_at.is_(None))
            .where(NewsArticle.source.in_(NATIVE_APP_LANG_SOURCES))
            .order_by(
                func.coalesce(NewsArticle.published_at, NewsArticle.created_at).desc(),
                NewsArticle.id.desc(),
            )
            .offset(native_keep)
        )
    ).all()
    stale_ids.update(int(article_id) for article_id in native_unreleased_ids)

    general_unreleased_ids = (
        await db.scalars(
            select(NewsArticle.id)
            .where(NewsArticle.released_at.is_(None))
            .where(~NewsArticle.source.in_(NATIVE_APP_LANG_SOURCES))
            .order_by(
                func.coalesce(NewsArticle.published_at, NewsArticle.created_at).desc(),
                NewsArticle.id.desc(),
            )
            .offset(general_keep)
        )
    ).all()
    stale_ids.update(int(article_id) for article_id in general_unreleased_ids)

    overflow_ids = (
        await db.scalars(
            select(NewsArticle.id)
            .order_by(
                native_priority,
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
                images_json=list(item.image_urls),
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
    await _ensure_article_enrichment_only(db, article=article)

    target_langs = _translation_targets_for_source(article.source)
    if not target_langs:
        return 0

    rows = (
        await db.execute(
            select(NewsArticleTranslation).where(NewsArticleTranslation.article_id == article.id)
        )
    ).scalars().all()
    rows_by_lang = {_normalize_lang(row.lang): row for row in rows}
    existing_langs = set(rows_by_lang)
    missing_langs = [lang for lang in target_langs if lang not in existing_langs]
    backfill_block_langs = [
        lang
        for lang in target_langs
        if lang in existing_langs
        and not _normalize_content_blocks(rows_by_lang[lang].content_blocks_json)
    ]
    if not missing_langs and not backfill_block_langs:
        return 0

    gemini_client = gemini or await build_gemini_client(db)
    if gemini_client is None:
        return 0

    source_blocks = _coerce_article_content_blocks(article) or _summary_to_content_blocks(
        article.raw_summary or "",
        article.raw_title or "",
    )
    snapshot = _article_to_feed_item(article)
    wrote = 0
    for lang in [*missing_langs, *backfill_block_langs]:
        translated = await _translate_item(gemini_client, snapshot, lang, content_blocks=source_blocks)
        if translated is None:
            continue
        title, summary, translated_blocks, model_used = translated
        existing_row = rows_by_lang.get(lang)
        if existing_row is not None:
            if not _normalize_content_blocks(existing_row.content_blocks_json):
                existing_row.content_blocks_json = list(translated_blocks)
            if not str(existing_row.title or "").strip():
                existing_row.title = title
            if not str(existing_row.summary or "").strip():
                existing_row.summary = summary
            if not str(existing_row.model or "").strip():
                existing_row.model = model_used
        else:
            await db.execute(
                insert(NewsArticleTranslation)
                .values(
                    article_id=article.id,
                    lang=lang,
                    title=title,
                    summary=summary,
                    content_blocks_json=list(translated_blocks),
                    model=model_used,
                )
                .on_conflict_do_nothing(index_elements=["article_id", "lang"])
            )
        wrote += 1
    return wrote


async def _ensure_article_enrichment_only(
    db: AsyncSession,
    *,
    article: NewsArticle,
) -> bool:
    current_image_urls = _coerce_article_image_urls(article)
    current_content_blocks = _coerce_article_content_blocks(article)
    article_extract = await _build_article_enrichment(
        summary=article.raw_summary or "",
        title=article.raw_title or "",
        url=article.url or "",
        current_image_urls=current_image_urls,
        current_content_blocks=current_content_blocks,
    )
    enriched_summary = article_extract.summary
    current_summary = _sanitize_preview_text(article.raw_summary or "")
    content_blocks_changed = article_extract.content_blocks != current_content_blocks
    if enriched_summary and enriched_summary != current_summary:
        article.raw_summary = enriched_summary
    if article_extract.image_urls != current_image_urls:
        article.images_json = list(article_extract.image_urls)
        article.image_url = _first_image_url(article_extract.image_urls) or None
    if content_blocks_changed:
        article.content_blocks_json = list(article_extract.content_blocks)
    changed = (
        enriched_summary
        and enriched_summary != current_summary
        or article_extract.image_urls != current_image_urls
        or content_blocks_changed
    )
    if changed:
        await db.flush()
    return bool(changed)


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


async def _translate_item(
    gemini: GeminiClient,
    item: FeedItem,
    lang: str,
    *,
    content_blocks: tuple[dict[str, str], ...] = (),
) -> tuple[str, str, tuple[dict[str, str], ...], str] | None:
    normalized_lang = _normalize_lang(lang)
    source_text = _clip_preview_text(
        _sanitize_preview_text(item.summary),
        max_chars=ARTICLE_SOURCE_MAX_CHARS,
    )
    if not source_text:
        source_text = _clip_preview_text(item.title, max_chars=ARTICLE_SOURCE_MAX_CHARS)
    normalized_blocks = _normalize_content_blocks(content_blocks) or _summary_to_content_blocks(
        source_text,
        item.title,
    )
    text_block_payload = [
        {
            "index": index,
            "text": str(block.get("text") or "").strip(),
        }
        for index, block in enumerate(normalized_blocks)
        if str(block.get("type") or "").strip().lower() == "text"
        and str(block.get("text") or "").strip()
    ]
    prompt_lang = TARGET_LANGUAGE_NAMES.get(normalized_lang, "English")
    prompt = f"""
Translate this crypto news into {prompt_lang}.

Return JSON only with these keys:
- title
- summary
- textBlocks

Rules:
- Do not wrap the JSON in markdown or code fences.
- summary must be a compact 2 to 4 sentence overview in the target language.
- textBlocks must be a JSON array.
- textBlocks must contain one translated entry for every source text block index you receive.
- Each textBlocks entry must have:
  - index
  - text
- Do not invent, remove, or reorder facts.
- Preserve numbers, tickers, company names, and market terms accurately.
- If the source is already in the target language, rewrite it cleanly in that language.
- Do not leave English sentences in non-English output.
- Translate each text block as a polished paragraph.

Title:
{item.title}

Summary:
{source_text}

Source text blocks:
{json.dumps(text_block_payload, ensure_ascii=False)}
""".strip()

    result = await gemini.generate_text(prompt=prompt, temperature=0.2)
    if result is None:
        return None

    data = _extract_json_object(result.text) or {}
    title = (data.get("title") or "").strip()
    summary = _sanitize_article_block_text(str(data.get("summary") or "").strip())
    translated_rows = data.get("textBlocks")
    translated_map: dict[int, str] = {}
    if isinstance(translated_rows, list):
        for row in translated_rows:
            if not isinstance(row, dict):
                continue
            try:
                index = int(row.get("index"))
            except Exception:
                continue
            text_value = _sanitize_article_block_text(str(row.get("text") or "").strip())
            if not text_value:
                continue
            translated_map[index] = text_value

    if not title:
        title = item.title.strip()
    translated_blocks: list[dict[str, str]] = []
    for index, block in enumerate(normalized_blocks):
        block_type = str(block.get("type") or "").strip().lower()
        if block_type == "image":
            translated_blocks.append(dict(block))
            continue
        translated_text = translated_map.get(index)
        if not translated_text:
            if normalized_lang != "en":
                continue
            translated_text = str(block.get("text") or "").strip()
        translated_blocks.append({"type": "text", "text": translated_text})

    normalized_translated_blocks = _normalize_content_blocks(translated_blocks)
    if not summary:
        summary = _summary_from_content_blocks(
            normalized_translated_blocks,
            fallback=source_text,
        )

    if normalized_lang != "en" and _looks_english(f"{title} {summary}"):
        return None
    return title[:512], summary[:2600], normalized_translated_blocks, result.model

from __future__ import annotations

import re

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.ai_provider_config_service import get_gemini_config
from app.services.gemini_service import GeminiClient


def _normalize_lang(lang: str) -> str:
    base = (lang or "").strip().lower()
    if not base:
        return "en"
    base = base.split("-", 1)[0]
    if base in {"en", "uz", "ru"}:
        return base
    return base


def _strip_control(text: str) -> str:
    return re.sub(r"\u0000", "", text or "").strip()


async def translate_text_via_gemini(
    db: AsyncSession,
    *,
    text: str,
    target_lang: str,
) -> tuple[str, str] | None:
    clean = _strip_control(text)
    if not clean:
        return "", ""

    lang = _normalize_lang(target_lang)
    if lang == "en":
        return clean, ""

    cfg = await get_gemini_config(db)
    if cfg is None:
        return None

    prompt_lang = "O'zbek" if lang == "uz" else ("Русский" if lang == "ru" else lang)
    prompt = f"""
Translate the text below into {prompt_lang}.

Rules:
- Output ONLY the translated text. No JSON, no markdown.
- Preserve paragraph breaks.
- Do not add commentary.

TEXT:
{clean}
""".strip()

    gemini = GeminiClient(cfg)
    res = await gemini.generate_text(prompt=prompt, temperature=0.1)
    if res is None:
        return None

    out = (res.text or "").strip()
    if not out:
        return None
    return out, res.model


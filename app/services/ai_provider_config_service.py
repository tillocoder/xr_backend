from __future__ import annotations

import os
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.entities import AiProviderConfig


@dataclass(frozen=True)
class GeminiConfig:
    api_key: str
    model: str


def _env(key: str) -> str:
    return (os.getenv(key) or "").strip()


async def get_gemini_config(db: AsyncSession) -> GeminiConfig | None:
    try:
        row = await db.scalar(
            select(AiProviderConfig)
            .where(AiProviderConfig.provider == "gemini")
            .where(AiProviderConfig.enabled.is_(True))
            .order_by(AiProviderConfig.updated_at.desc())
            .limit(1)
        )
        if row is not None:
            api_key = (row.api_key or "").strip()
            model = (row.model or "").strip() or "gemini-1.5-flash"
            if api_key:
                return GeminiConfig(api_key=api_key, model=model)
    except SQLAlchemyError:
        # Database not migrated yet / table missing / transient DB issue.
        # Fall back to env vars so the app can still boot.
        pass

    api_key = _env("XR_GEMINI_API_KEY") or _env("GEMINI_API_KEY")
    if not api_key:
        return None
    model = _env("XR_GEMINI_MODEL") or _env("GEMINI_MODEL") or "gemini-1.5-flash"
    return GeminiConfig(api_key=api_key, model=model)

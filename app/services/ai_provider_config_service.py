from __future__ import annotations

import os
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.entities import AiProviderConfig


DEFAULT_GEMINI_MODEL = "gemini-3-flash-preview"
MAX_GEMINI_API_KEYS = 5


@dataclass(frozen=True)
class GeminiConfig:
    id: int | None
    api_key: str
    model: str
    label: str
    sort_order: int


def _env(key: str) -> str:
    return (os.getenv(key) or "").strip()


def _default_label(sort_order: int) -> str:
    return f"Gemini Key {max(1, int(sort_order or 1))}"


def _clean_label(label: str | None, sort_order: int) -> str:
    raw = str(label or "").strip()
    return raw[:64] if raw else _default_label(sort_order)


def _default_model() -> str:
    return _env("XR_GEMINI_MODEL") or _env("GEMINI_MODEL") or DEFAULT_GEMINI_MODEL


def mask_api_key(api_key: str | None) -> str:
    raw = str(api_key or "").strip()
    if not raw:
        return ""
    if len(raw) <= 8:
        return "*" * len(raw)
    return f"{raw[:4]}...{raw[-4:]}"


async def count_gemini_config_rows(db: AsyncSession) -> int:
    return int(
        (
            await db.scalar(
                select(func.count(AiProviderConfig.id)).where(
                    AiProviderConfig.provider == "gemini"
                )
            )
        )
        or 0
    )


async def list_gemini_config_rows(
    db: AsyncSession,
    *,
    include_disabled: bool = False,
) -> list[AiProviderConfig]:
    stmt = (
        select(AiProviderConfig)
        .where(AiProviderConfig.provider == "gemini")
        .order_by(
            AiProviderConfig.sort_order.asc(),
            AiProviderConfig.updated_at.desc(),
            AiProviderConfig.id.asc(),
        )
    )
    if not include_disabled:
        stmt = stmt.where(AiProviderConfig.enabled.is_(True))
    return list((await db.scalars(stmt)).all())


async def place_gemini_config(
    db: AsyncSession,
    row: AiProviderConfig,
    *,
    desired_order: int | None = None,
) -> None:
    rows = await list_gemini_config_rows(db, include_disabled=True)
    rows = [item for item in rows if item.id != row.id]
    target = max(1, min(int(desired_order or row.sort_order or 1), len(rows) + 1))
    rows.insert(target - 1, row)

    for index, item in enumerate(rows, start=1):
        item.sort_order = index
        item.label = _clean_label(item.label, index)


async def rebalance_gemini_config_rows(db: AsyncSession) -> None:
    rows = await list_gemini_config_rows(db, include_disabled=True)
    for index, item in enumerate(rows, start=1):
        item.sort_order = index
        item.label = _clean_label(item.label, index)


def _row_to_gemini_config(row: AiProviderConfig) -> GeminiConfig | None:
    api_key = (row.api_key or "").strip()
    if not api_key:
        return None
    return GeminiConfig(
        id=int(row.id),
        api_key=api_key,
        model=(row.model or "").strip() or DEFAULT_GEMINI_MODEL,
        label=_clean_label(row.label, row.sort_order),
        sort_order=max(1, int(row.sort_order or 1)),
    )


async def list_gemini_configs(db: AsyncSession) -> list[GeminiConfig]:
    try:
        rows = await list_gemini_config_rows(db, include_disabled=False)
        configs = [cfg for row in rows if (cfg := _row_to_gemini_config(row)) is not None]
        if configs:
            return configs
    except SQLAlchemyError:
        # Database not migrated yet / table missing / transient DB issue.
        # Fall back to env vars so the app can still boot.
        pass

    api_key = _env("XR_GEMINI_API_KEY") or _env("GEMINI_API_KEY")
    if not api_key:
        return []
    return [
        GeminiConfig(
            id=None,
            api_key=api_key,
            model=_default_model(),
            label="Env Gemini Key",
            sort_order=MAX_GEMINI_API_KEYS + 1,
        )
    ]


async def get_gemini_config(db: AsyncSession) -> GeminiConfig | None:
    configs = await list_gemini_configs(db)
    return configs[0] if configs else None


async def ensure_gemini_config_row(db: AsyncSession) -> AiProviderConfig:
    row = await db.scalar(
        select(AiProviderConfig)
        .where(AiProviderConfig.provider == "gemini")
        .order_by(AiProviderConfig.sort_order.asc(), AiProviderConfig.id.asc())
        .limit(1)
    )
    if row is not None:
        if not str(row.label or "").strip() or int(row.sort_order or 0) < 1:
            row.label = _clean_label(row.label, row.sort_order or 1)
            row.sort_order = max(1, int(row.sort_order or 1))
            await db.commit()
            await db.refresh(row)
        return row

    row = AiProviderConfig(
        provider="gemini",
        label=_default_label(1),
        api_key=_env("XR_GEMINI_API_KEY") or _env("GEMINI_API_KEY") or None,
        model=_default_model(),
        sort_order=1,
        enabled=True,
    )
    db.add(row)
    await place_gemini_config(db, row, desired_order=1)
    await db.commit()
    await db.refresh(row)
    return row

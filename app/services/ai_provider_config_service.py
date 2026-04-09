from __future__ import annotations

import os
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.entities import AiProviderConfig


DEFAULT_GEMINI_MODEL = "gemini-2.5-flash"
MAX_GEMINI_API_KEYS = 5
GEMINI_SCOPE_DEFAULT = "default"
GEMINI_SCOPE_PORTFOLIO = "portfolio"
_GEMINI_USAGE_SCOPES = {
    GEMINI_SCOPE_DEFAULT,
    GEMINI_SCOPE_PORTFOLIO,
}


@dataclass(frozen=True)
class GeminiConfig:
    id: int | None
    api_key: str
    model: str
    label: str
    sort_order: int
    usage_scope: str


def _env(key: str) -> str:
    raw = (os.getenv(key) or "").strip()
    if raw:
        return raw

    settings = get_settings()
    settings_key = key.lower()
    if settings_key.startswith("xr_"):
        settings_key = settings_key[3:]
    return str(getattr(settings, settings_key, "") or "").strip()


def _default_label(sort_order: int) -> str:
    return f"Gemini Key {max(1, int(sort_order or 1))}"


def normalize_gemini_usage_scope(value: str | None) -> str:
    raw = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    if not raw:
        return GEMINI_SCOPE_DEFAULT
    if raw in _GEMINI_USAGE_SCOPES:
        return raw
    raise ValueError(f"Unsupported Gemini usage scope: {value!r}")


def _clean_label(label: str | None, sort_order: int) -> str:
    raw = str(label or "").strip()
    return raw[:64] if raw else _default_label(sort_order)


def _scope_env_candidates(base_name: str, usage_scope: str) -> list[str]:
    scope = normalize_gemini_usage_scope(usage_scope)
    if scope == GEMINI_SCOPE_DEFAULT:
        return [f"XR_{base_name}", base_name]
    suffix = scope.upper()
    return [f"XR_{base_name}_{suffix}", f"{base_name}_{suffix}"]


def _scope_env(base_name: str, usage_scope: str) -> str:
    for candidate in _scope_env_candidates(base_name, usage_scope):
        value = _env(candidate)
        if value:
            return value
    return ""


def _default_model(usage_scope: str = GEMINI_SCOPE_DEFAULT) -> str:
    return _scope_env("GEMINI_MODEL", usage_scope) or DEFAULT_GEMINI_MODEL


def mask_api_key(api_key: str | None) -> str:
    raw = str(api_key or "").strip()
    if not raw:
        return ""
    if len(raw) <= 8:
        return "*" * len(raw)
    return f"{raw[:4]}...{raw[-4:]}"


async def count_gemini_config_rows(
    db: AsyncSession,
    *,
    usage_scope: str = GEMINI_SCOPE_DEFAULT,
    exclude_id: int | None = None,
) -> int:
    scope = normalize_gemini_usage_scope(usage_scope)
    stmt = select(func.count(AiProviderConfig.id)).where(
        AiProviderConfig.provider == "gemini",
        AiProviderConfig.usage_scope == scope,
    )
    if exclude_id is not None:
        stmt = stmt.where(AiProviderConfig.id != int(exclude_id))
    return int(
        (
            await db.scalar(stmt)
        )
        or 0
    )


async def list_gemini_config_rows(
    db: AsyncSession,
    *,
    include_disabled: bool = False,
    usage_scope: str = GEMINI_SCOPE_DEFAULT,
) -> list[AiProviderConfig]:
    scope = normalize_gemini_usage_scope(usage_scope)
    stmt = (
        select(AiProviderConfig)
        .where(
            AiProviderConfig.provider == "gemini",
            AiProviderConfig.usage_scope == scope,
        )
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
    scope = normalize_gemini_usage_scope(row.usage_scope)
    row.usage_scope = scope
    rows = await list_gemini_config_rows(
        db,
        include_disabled=True,
        usage_scope=scope,
    )
    rows = [item for item in rows if item.id != row.id]
    target = max(1, min(int(desired_order or row.sort_order or 1), len(rows) + 1))
    rows.insert(target - 1, row)

    for index, item in enumerate(rows, start=1):
        item.sort_order = index
        item.label = _clean_label(item.label, index)


async def rebalance_gemini_config_rows(
    db: AsyncSession,
    *,
    usage_scope: str = GEMINI_SCOPE_DEFAULT,
) -> None:
    rows = await list_gemini_config_rows(
        db,
        include_disabled=True,
        usage_scope=usage_scope,
    )
    for index, item in enumerate(rows, start=1):
        item.sort_order = index
        item.label = _clean_label(item.label, index)


def _row_to_gemini_config(row: AiProviderConfig) -> GeminiConfig | None:
    api_key = (row.api_key or "").strip()
    if not api_key:
        return None
    scope = normalize_gemini_usage_scope(row.usage_scope)
    return GeminiConfig(
        id=int(row.id),
        api_key=api_key,
        model=(row.model or "").strip() or DEFAULT_GEMINI_MODEL,
        label=_clean_label(row.label, row.sort_order),
        sort_order=max(1, int(row.sort_order or 1)),
        usage_scope=scope,
    )


def _fallback_env_config(usage_scope: str) -> GeminiConfig | None:
    api_key = _scope_env("GEMINI_API_KEY", usage_scope)
    if not api_key:
        return None
    scope = normalize_gemini_usage_scope(usage_scope)
    return GeminiConfig(
        id=None,
        api_key=api_key,
        model=_default_model(scope),
        label="Env Gemini Key" if scope == GEMINI_SCOPE_DEFAULT else f"Env {scope.title()} Gemini Key",
        sort_order=MAX_GEMINI_API_KEYS + 1,
        usage_scope=scope,
    )


async def list_gemini_configs(
    db: AsyncSession,
    *,
    usage_scope: str = GEMINI_SCOPE_DEFAULT,
    fallback_scopes: tuple[str, ...] = (),
) -> list[GeminiConfig]:
    scopes = [normalize_gemini_usage_scope(usage_scope)]
    for fallback in fallback_scopes:
        normalized = normalize_gemini_usage_scope(fallback)
        if normalized not in scopes:
            scopes.append(normalized)

    collected: list[GeminiConfig] = []
    try:
        for scope in scopes:
            rows = await list_gemini_config_rows(
                db,
                include_disabled=False,
                usage_scope=scope,
            )
            configs = [cfg for row in rows if (cfg := _row_to_gemini_config(row)) is not None]
            if configs:
                collected.extend(configs)
    except SQLAlchemyError:
        # Database not migrated yet / table missing / transient DB issue.
        # Fall back to env vars so the app can still boot.
        pass

    if collected:
        return collected

    for scope in scopes:
        cfg = _fallback_env_config(scope)
        if cfg is not None:
            collected.append(cfg)
    if collected:
        return collected
    return []


async def get_gemini_config(
    db: AsyncSession,
    *,
    usage_scope: str = GEMINI_SCOPE_DEFAULT,
    fallback_scopes: tuple[str, ...] = (),
) -> GeminiConfig | None:
    configs = await list_gemini_configs(
        db,
        usage_scope=usage_scope,
        fallback_scopes=fallback_scopes,
    )
    return configs[0] if configs else None


async def ensure_gemini_config_row(
    db: AsyncSession,
    *,
    usage_scope: str = GEMINI_SCOPE_DEFAULT,
) -> AiProviderConfig:
    scope = normalize_gemini_usage_scope(usage_scope)
    row = await db.scalar(
        select(AiProviderConfig)
        .where(
            AiProviderConfig.provider == "gemini",
            AiProviderConfig.usage_scope == scope,
        )
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
        usage_scope=scope,
        label=_default_label(1),
        api_key=_scope_env("GEMINI_API_KEY", scope) or None,
        model=_default_model(scope),
        sort_order=1,
        enabled=True,
    )
    db.add(row)
    await place_gemini_config(db, row, desired_order=1)
    await db.commit()
    await db.refresh(row)
    return row

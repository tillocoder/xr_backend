from __future__ import annotations

import contextlib
import logging

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


logger = logging.getLogger(__name__)


async def try_acquire_session_advisory_lock(
    db: AsyncSession,
    lock_key: int,
    *,
    lock_name: str,
) -> bool:
    try:
        return bool(
            await db.scalar(
                text("SELECT pg_try_advisory_lock(:lock_key)"),
                {"lock_key": int(lock_key)},
            )
        )
    except Exception:
        logger.warning(
            "db_runtime_lock_unavailable",
            exc_info=True,
            extra={"lock_name": lock_name, "lock_key": int(lock_key)},
        )
        return False


async def release_session_advisory_lock(db: AsyncSession, lock_key: int) -> None:
    with contextlib.suppress(Exception):
        await db.execute(
            text("SELECT pg_advisory_unlock(:lock_key)"),
            {"lock_key": int(lock_key)},
        )

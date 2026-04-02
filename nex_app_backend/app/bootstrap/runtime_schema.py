from __future__ import annotations

from app.db.base import Base
from app.db.session import engine


async def ensure_runtime_schema(auto_create_schema: bool) -> None:
    if not auto_create_schema:
        return

    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)


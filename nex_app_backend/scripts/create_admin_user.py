from __future__ import annotations

import asyncio

from sqlalchemy import select

from app.core.config import get_settings
from app.db.base import Base
from app.db.session import SessionLocal, engine
from app.services.user_service import create_user
from app.models.entities import User


async def main() -> None:
    settings = get_settings()

    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    async with SessionLocal() as session:
        existing = await session.execute(
            select(User).where(User.email == settings.admin_email.lower().strip())
        )
        if existing.scalar_one_or_none() is not None:
            print(f"Admin user already exists: {settings.admin_email}")
            return

        await create_user(
            session,
            email=settings.admin_email,
            full_name="Nex Admin",
            password=settings.admin_password,
            role="admin",
        )
        await session.commit()
        print(f"Admin user created: {settings.admin_email}")


if __name__ == "__main__":
    asyncio.run(main())


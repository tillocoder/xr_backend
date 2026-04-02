from __future__ import annotations

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import hash_password
from app.models.entities import User


async def get_user_by_email(session: AsyncSession, email: str) -> User | None:
    statement: Select[tuple[User]] = select(User).where(User.email == email.lower().strip())
    result = await session.execute(statement)
    return result.scalar_one_or_none()


async def get_user_by_id(session: AsyncSession, user_id: str) -> User | None:
    statement: Select[tuple[User]] = select(User).where(User.id == user_id)
    result = await session.execute(statement)
    return result.scalar_one_or_none()


async def create_user(
    session: AsyncSession,
    *,
    email: str,
    full_name: str,
    password: str,
    role: str = "user",
) -> User:
    user = User(
        email=email.lower().strip(),
        full_name=full_name.strip(),
        hashed_password=hash_password(password),
        role=role,
        is_active=True,
    )
    session.add(user)
    await session.flush()
    await session.refresh(user)
    return user


async def set_password(session: AsyncSession, user: User, new_password: str) -> User:
    user.hashed_password = hash_password(new_password)
    session.add(user)
    await session.flush()
    await session.refresh(user)
    return user


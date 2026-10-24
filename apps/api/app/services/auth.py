import asyncio
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import (
    DUMMY_PASSWORD_HASH,
    hash_password,
    hash_session_token,
    new_session_token,
    password_needs_rehash,
    verify_password,
)
from app.db.models import AuthSession, User


class EmailTakenError(Exception):
    pass


async def create_user(
    db: AsyncSession, *, email: str, password: str, display_name: str | None
) -> User:
    # Argon2 is deliberately slow; keep it off the event loop.
    password_hash = await asyncio.to_thread(hash_password, password)
    user = User(email=email, password_hash=password_hash, display_name=display_name)
    db.add(user)
    try:
        await db.flush()
    except IntegrityError as exc:
        await db.rollback()
        raise EmailTakenError from exc
    return user


async def authenticate(db: AsyncSession, *, email: str, password: str) -> User | None:
    user = await db.scalar(select(User).where(User.email == email))
    if user is None or not user.is_active:
        await asyncio.to_thread(verify_password, DUMMY_PASSWORD_HASH, password)
        return None
    if not await asyncio.to_thread(verify_password, user.password_hash, password):
        return None
    if password_needs_rehash(user.password_hash):
        user.password_hash = await asyncio.to_thread(hash_password, password)
    return user


def start_session(db: AsyncSession, user: User, *, ttl: timedelta, user_agent: str | None) -> str:
    token = new_session_token()
    db.add(
        AuthSession(
            id=hash_session_token(token),
            user_id=user.id,
            expires_at=datetime.now(UTC) + ttl,
            user_agent=user_agent[:255] if user_agent else None,
        )
    )
    return token


async def user_for_session(db: AsyncSession, token: str) -> User | None:
    stmt = (
        select(User)
        .join(AuthSession, AuthSession.user_id == User.id)
        .where(
            AuthSession.id == hash_session_token(token),
            AuthSession.expires_at > datetime.now(UTC),
            User.is_active.is_(True),
        )
    )
    return await db.scalar(stmt)


async def end_session(db: AsyncSession, token: str) -> None:
    await db.execute(delete(AuthSession).where(AuthSession.id == hash_session_token(token)))


async def delete_expired_sessions(db: AsyncSession) -> int:
    deleted = await db.scalars(
        delete(AuthSession)
        .where(AuthSession.expires_at <= datetime.now(UTC))
        .returning(AuthSession.id)
    )
    return len(deleted.all())

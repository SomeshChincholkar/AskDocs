"""Password hashing, JWT creation, and token-backed user lookup."""

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import UUID

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from fastapi import HTTPException, status
from jwt import InvalidTokenError
from psycopg_pool import AsyncConnectionPool

from app.config import Settings

_password_hasher = PasswordHasher()
_DUMMY_PASSWORD_HASH = (
    "$argon2id$v=19$m=65536,t=3,p=4$3ko5NODnyG0S7moLpZvFnA$"
    "2q4V9K2BQOcSYTpV/b/0B1akgLQPuS9GzFmTp8wNjgA"
)


@dataclass(frozen=True)
class AuthenticatedUser:
    id: UUID
    username: str | None
    is_anonymous: bool
    token_version: int


class AuthError(HTTPException):
    """Authentication failure with a stable machine-readable code."""

    def __init__(self, detail: str, code: str) -> None:
        super().__init__(status_code=status.HTTP_401_UNAUTHORIZED, detail=detail)
        self.code = code


def _hash_password(password: str) -> str:
    return _password_hasher.hash(password)


def _verify_password(password_hash: str, password: str) -> bool:
    try:
        return _password_hasher.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


async def hash_password(password: str) -> str:
    """Hash a password without blocking the async event loop."""
    return await asyncio.to_thread(_hash_password, password)


async def verify_password(password_hash: str, password: str) -> bool:
    """Verify a password hash without blocking the async event loop."""
    return await asyncio.to_thread(_verify_password, password_hash, password)


async def verify_dummy_password(password: str) -> None:
    """Perform a password verification for unknown usernames."""
    await verify_password(_DUMMY_PASSWORD_HASH, password)


def create_access_token(
    user: AuthenticatedUser,
    settings: Settings,
) -> tuple[str, datetime]:
    """Create an anonymous or registered-user bearer token."""
    now = datetime.now(timezone.utc)
    days = settings.anon_jwt_expire_days if user.is_anonymous else settings.jwt_expire_days
    expires_at = now + timedelta(days=days)
    claims = {
        "sub": str(user.id),
        "typ": "anon" if user.is_anonymous else "user",
        "ver": user.token_version,
        "iat": now,
        "exp": expires_at,
    }
    token = jwt.encode(
        claims,
        settings.jwt_secret.get_secret_value(),
        algorithm=settings.jwt_algorithm,
    )
    return token, expires_at


def _authentication_failure(strict: bool) -> None:
    if strict:
        raise AuthError("Session expired", "token_invalid")


async def get_user_from_token(
    pool: AsyncConnectionPool,
    settings: Settings,
    token: str,
    strict: bool = True,
) -> AuthenticatedUser | None:
    """Verify a JWT and its current database account state."""
    try:
        claims = jwt.decode(
            token,
            settings.jwt_secret.get_secret_value(),
            algorithms=[settings.jwt_algorithm],
            options={"require": ["sub", "typ", "ver", "iat", "exp"]},
        )
        user_id = UUID(claims["sub"])
        token_type = claims["typ"]
        token_version = claims["ver"]
        if (
            token_type not in {"anon", "user"}
            or isinstance(token_version, bool)
            or not isinstance(token_version, int)
            or token_version < 0
        ):
            raise ValueError("Invalid token claims.")
    except (InvalidTokenError, KeyError, TypeError, ValueError):
        _authentication_failure(strict)
        return None

    async with pool.connection() as connection:
        cursor = await connection.execute(
            """
            SELECT id, username, is_anonymous, token_version
            FROM users WHERE id = %s
            """,
            (user_id,),
        )
        row = await cursor.fetchone()
    if row is None:
        _authentication_failure(strict)
        return None

    is_anonymous = row["is_anonymous"]
    expected_type = "anon" if is_anonymous else "user"
    if row["token_version"] != token_version or token_type != expected_type:
        _authentication_failure(strict)
        return None
    return AuthenticatedUser(
        id=row["id"],
        username=row["username"],
        is_anonymous=is_anonymous,
        token_version=row["token_version"],
    )


async def get_user_from_token_soft(
    pool: AsyncConnectionPool,
    settings: Settings,
    token: str | None,
) -> AuthenticatedUser | None:
    """Resolve a token for auth flows without failing on missing or invalid JWTs."""
    if not token:
        return None
    return await get_user_from_token(pool, settings, token, strict=False)

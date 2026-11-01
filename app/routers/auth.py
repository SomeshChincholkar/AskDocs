"""Anonymous identity creation and username/password authentication."""

import logging
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request, status
from psycopg.errors import UniqueViolation
from psycopg_pool import AsyncConnectionPool

from app.config import Settings
from app.deps import (
    CurrentUser,
    client_ip,
    enforce_rate_limit,
    get_app_settings,
    get_current_user,
    get_pool,
)
from app.schemas import AuthCredentials, AuthMe, AuthResponse
from app.services.auth import (
    AuthenticatedUser,
    create_access_token,
    get_user_from_token_soft,
    hash_password,
    verify_dummy_password,
    verify_password,
)
from app.services.merge import merge_anonymous_data

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/auth", tags=["auth"])
INVALID_CREDENTIALS = "Invalid username or password"
LOGIN_WINDOW_SECONDS = 15 * 60


def _authorization_token(request: Request) -> str | None:
    authorization = request.headers.get("authorization", "")
    scheme, separator, token = authorization.partition(" ")
    if separator and scheme.lower() == "bearer" and token.strip():
        return token.strip()
    return None


def _token_response(
    user: AuthenticatedUser,
    settings: Settings,
    merged: dict[str, int] | None = None,
) -> dict[str, object]:
    token, expires_at = create_access_token(user, settings)
    return {
        "access_token": token,
        "token_type": "bearer",
        "expires_at": expires_at,
        "user": {
            "id": user.id,
            "username": user.username,
            "is_anonymous": user.is_anonymous,
        },
        "merged": merged,
    }


def _enforce_auth_limits(request: Request, username: str, settings: Settings) -> None:
    limit = settings.login_attempts_per_15_min
    enforce_rate_limit(
        request,
        "auth-ip",
        limit,
        window_seconds=LOGIN_WINDOW_SECONDS,
        subject=client_ip(request),
    )
    enforce_rate_limit(
        request,
        "auth-username",
        limit,
        window_seconds=LOGIN_WINDOW_SECONDS,
        subject=username.casefold(),
    )


@router.post(
    "/anonymous",
    response_model=AuthResponse,
    response_model_exclude_none=True,
)
async def create_anonymous_user(
    request: Request,
    pool: Annotated[AsyncConnectionPool, Depends(get_pool)],
    settings: Annotated[Settings, Depends(get_app_settings)],
) -> dict[str, object]:
    """Create an anonymous account and return its signed bearer token."""
    enforce_rate_limit(
        request,
        "anonymous-create",
        settings.anon_create_per_hour,
    )
    user_id = uuid4()
    async with pool.connection() as connection:
        cursor = await connection.execute(
            """
            INSERT INTO users (id, is_anonymous)
            VALUES (%s, true)
            RETURNING id, username, is_anonymous, token_version
            """,
            (user_id,),
        )
        row = await cursor.fetchone()
    user = AuthenticatedUser(
        id=row["id"],
        username=row["username"],
        is_anonymous=row["is_anonymous"],
        token_version=row["token_version"],
    )
    return _token_response(user, settings)


@router.post("/register", response_model=AuthResponse, status_code=status.HTTP_201_CREATED)
async def register(
    request: Request,
    credentials: AuthCredentials,
    pool: Annotated[AsyncConnectionPool, Depends(get_pool)],
    settings: Annotated[Settings, Depends(get_app_settings)],
) -> dict[str, object]:
    """Create a registered user, upgrading the current anonymous identity if possible."""
    _enforce_auth_limits(request, credentials.username, settings)
    anonymous_user = await get_user_from_token_soft(
        pool,
        settings,
        _authorization_token(request),
    )
    password_hash = await hash_password(credentials.password)
    try:
        async with pool.connection() as connection:
            async with connection.transaction():
                registered_row = None
                if (
                    anonymous_user is not None
                    and anonymous_user.is_anonymous
                    and anonymous_user.username is None
                ):
                    lock_cursor = await connection.execute(
                        """
                        SELECT id FROM users
                        WHERE id = %s AND is_anonymous = true AND username IS NULL
                        FOR UPDATE
                        """,
                        (anonymous_user.id,),
                    )
                    locked_user = await lock_cursor.fetchone()
                    if locked_user is not None:
                        update_cursor = await connection.execute(
                            """
                            UPDATE users
                            SET username = %s, password_hash = %s, is_anonymous = false,
                                token_version = token_version + 1
                            WHERE id = %s
                            RETURNING id, username, is_anonymous, token_version
                            """,
                            (credentials.username, password_hash, anonymous_user.id),
                        )
                        registered_row = await update_cursor.fetchone()

                if registered_row is None:
                    insert_cursor = await connection.execute(
                        """
                        INSERT INTO users (username, password_hash, is_anonymous)
                        VALUES (%s, %s, false)
                        RETURNING id, username, is_anonymous, token_version
                        """,
                        (credentials.username, password_hash),
                    )
                    registered_row = await insert_cursor.fetchone()

                session_cursor = await connection.execute(
                    "SELECT count(*) AS total FROM sessions WHERE user_id = %s",
                    (registered_row["id"],),
                )
                document_cursor = await connection.execute(
                    "SELECT count(*) AS total FROM documents WHERE user_id = %s",
                    (registered_row["id"],),
                )
                merged = {
                    "sessions": (await session_cursor.fetchone())["total"],
                    "documents": (await document_cursor.fetchone())["total"],
                }
    except UniqueViolation as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="That username is already taken.",
        ) from error

    user = AuthenticatedUser(
        id=registered_row["id"],
        username=registered_row["username"],
        is_anonymous=registered_row["is_anonymous"],
        token_version=registered_row["token_version"],
    )
    return _token_response(user, settings, merged)


@router.post("/login", response_model=AuthResponse)
async def login(
    request: Request,
    credentials: AuthCredentials,
    pool: Annotated[AsyncConnectionPool, Depends(get_pool)],
    settings: Annotated[Settings, Depends(get_app_settings)],
) -> dict[str, object]:
    """Verify credentials and merge a valid anonymous visitor into the account."""
    _enforce_auth_limits(request, credentials.username, settings)
    anonymous_user = await get_user_from_token_soft(
        pool,
        settings,
        _authorization_token(request),
    )
    async with pool.connection() as connection:
        cursor = await connection.execute(
            """
            SELECT id, username, is_anonymous, token_version, password_hash
            FROM users
            WHERE lower(username) = lower(%s) AND is_anonymous = false
            """,
            (credentials.username,),
        )
        account = await cursor.fetchone()

    if account is None or not account["password_hash"]:
        await verify_dummy_password(credentials.password)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=INVALID_CREDENTIALS,
        )
    if not await verify_password(account["password_hash"], credentials.password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=INVALID_CREDENTIALS,
        )

    merged = {"sessions": 0, "documents": 0}
    if (
        anonymous_user is not None
        and anonymous_user.is_anonymous
        and anonymous_user.username is None
        and anonymous_user.id != account["id"]
    ):
        try:
            merged = await merge_anonymous_data(pool, anonymous_user.id, account["id"])
        except Exception:
            logger.exception("Anonymous account merge failed; login will continue.")
            merged = {"sessions": 0, "documents": 0}

    user = AuthenticatedUser(
        id=account["id"],
        username=account["username"],
        is_anonymous=False,
        token_version=account["token_version"],
    )
    return _token_response(user, settings, merged)


@router.get("/me", response_model=AuthMe)
async def auth_me(
    user: Annotated[CurrentUser, Depends(get_current_user)],
) -> dict[str, object]:
    """Return the identity carried by the current bearer token."""
    return {
        "user_id": user.id,
        "username": user.username,
        "is_anonymous": user.is_anonymous,
    }


@router.post("/logout-all")
async def logout_all(
    user: Annotated[CurrentUser, Depends(get_current_user)],
    pool: Annotated[AsyncConnectionPool, Depends(get_pool)],
) -> dict[str, bool]:
    """Invalidate every registered-user token by incrementing token_version."""
    if user.is_anonymous:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Register or log in to use this action.",
        )
    async with pool.connection() as connection:
        await connection.execute(
            "UPDATE users SET token_version = token_version + 1 WHERE id = %s",
            (user.id,),
        )
    return {"ok": True}

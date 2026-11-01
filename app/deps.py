"""Anonymous-user identity and in-memory request rate limiting."""

import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Annotated
from uuid import UUID

from fastapi import Depends, HTTPException, Request, status
from psycopg_pool import AsyncConnectionPool

from app.config import Settings
from app.services.auth import AuthError, get_user_from_token


@dataclass(frozen=True)
class CurrentUser:
    id: UUID
    username: str | None
    is_anonymous: bool


class SlidingWindowRateLimiter:
    """Thread-safe per-key sliding-window limiter."""

    def __init__(self) -> None:
        self._events: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(
        self,
        key: str,
        limit: int,
        window_seconds: int = 3600,
        now: float | None = None,
    ) -> bool:
        timestamp = time.monotonic() if now is None else now
        cutoff = timestamp - window_seconds
        with self._lock:
            events = self._events[key]
            while events and events[0] <= cutoff:
                events.popleft()
            if len(events) >= limit:
                return False
            events.append(timestamp)
            return True


rate_limiter = SlidingWindowRateLimiter()


def get_pool(request: Request) -> AsyncConnectionPool:
    """Return the application pool or report missing database configuration."""
    pool = request.app.state.pool
    if pool is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database is not configured.",
        )
    return pool


def get_app_settings(request: Request) -> Settings:
    """Return the settings instance stored on application state."""
    return request.app.state.settings


def client_ip(request: Request) -> str:
    """Return the first forwarded address, falling back to the socket peer."""
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        address = forwarded.split(",", maxsplit=1)[0].strip()
        if address:
            return address
    return request.client.host if request.client else "unknown"


async def get_current_user(
    request: Request,
    pool: Annotated[AsyncConnectionPool, Depends(get_pool)],
    settings: Annotated[Settings, Depends(get_app_settings)],
) -> CurrentUser:
    """Resolve the user exclusively from a valid bearer JWT."""
    authorization = request.headers.get("authorization")
    if not authorization:
        raise AuthError("Not authenticated", "no_token")
    scheme, separator, token = authorization.partition(" ")
    if not separator or scheme.lower() != "bearer" or not token.strip():
        raise AuthError("Session expired", "token_invalid")
    authenticated = await get_user_from_token(pool, settings, token.strip())
    if authenticated is None:
        raise AuthError("Session expired", "token_invalid")
    async with pool.connection() as connection:
        await connection.execute(
            "UPDATE users SET last_seen_at = now() "
            "WHERE id = %s AND last_seen_at < now() - interval '5 minutes'",
            (authenticated.id,),
        )
    return CurrentUser(
        id=authenticated.id,
        username=authenticated.username,
        is_anonymous=authenticated.is_anonymous,
    )


def enforce_rate_limit(
    request: Request,
    action: str,
    limit: int,
    window_seconds: int = 3600,
    subject: str | None = None,
) -> None:
    """Raise HTTP 429 when an IP or named subject exceeds an action's limit."""
    key = (
        f"{action}:{subject}"
        if subject is not None
        else f"{client_ip(request)}:{action}"
    )
    if not rate_limiter.allow(key, limit, window_seconds):
        label = action[:-1] if action.endswith("s") else action
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Too many {label} attempts. Please try again later.",
        )

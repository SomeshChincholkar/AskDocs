"""Anonymous-user identity and in-memory request rate limiting."""

import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import Depends, HTTPException, Request, Response, status
from psycopg_pool import AsyncConnectionPool

from app.config import Settings


@dataclass(frozen=True)
class CurrentUser:
    id: UUID
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
    response: Response,
    pool: Annotated[AsyncConnectionPool, Depends(get_pool)],
    settings: Annotated[Settings, Depends(get_app_settings)],
) -> CurrentUser:
    """Resolve the anonymous cookie or create an anonymous user."""
    raw_id = request.cookies.get(settings.cookie_name)
    user_id: UUID | None = None
    if raw_id:
        try:
            user_id = UUID(raw_id)
        except ValueError:
            user_id = None

    async with pool.connection() as connection:
        if user_id is not None:
            row = await (
                await connection.execute(
                    "SELECT id, is_anonymous FROM users WHERE id = %s",
                    (user_id,),
                )
            ).fetchone()
        else:
            row = None

        if row is None:
            user_id = uuid4()
            await connection.execute(
                "INSERT INTO users (id, is_anonymous) VALUES (%s, true)",
                (user_id,),
            )
            response.set_cookie(
                key=settings.cookie_name,
                value=str(user_id),
                max_age=60 * 60 * 24 * 365,
                httponly=True,
                samesite="lax",
                secure=settings.secure_cookies,
                path="/",
            )
            return CurrentUser(id=user_id, is_anonymous=True)

        await connection.execute(
            "UPDATE users SET last_seen_at = now() "
            "WHERE id = %s AND last_seen_at < now() - interval '5 minutes'",
            (user_id,),
        )
    return CurrentUser(id=row["id"], is_anonymous=row["is_anonymous"])


def enforce_rate_limit(request: Request, action: str, limit: int) -> None:
    """Raise HTTP 429 when an IP exceeds an action's hourly limit."""
    key = f"{client_ip(request)}:{action}"
    if not rate_limiter.allow(key, limit):
        label = action[:-1] if action.endswith("s") else action
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Too many {label} requests. Please try again later.",
        )

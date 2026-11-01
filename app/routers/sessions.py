"""CRUD routes for user-owned chat sessions."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from psycopg_pool import AsyncConnectionPool

from app.deps import CurrentUser, get_current_user, get_pool
from app.schemas import SessionCreate, SessionInfo, SessionRename

router = APIRouter(prefix="/api/sessions", tags=["sessions"])


@router.get("", response_model=list[SessionInfo])
async def list_sessions(
    user: Annotated[CurrentUser, Depends(get_current_user)],
    pool: Annotated[AsyncConnectionPool, Depends(get_pool)],
) -> list[dict[str, object]]:
    async with pool.connection() as connection:
        cursor = await connection.execute(
            """
            SELECT id, title, created_at, updated_at
            FROM sessions WHERE user_id = %s
            ORDER BY updated_at DESC
            """,
            (user.id,),
        )
        return list(await cursor.fetchall())


@router.post("", response_model=SessionInfo, status_code=status.HTTP_201_CREATED)
async def create_session(
    body: SessionCreate,
    user: Annotated[CurrentUser, Depends(get_current_user)],
    pool: Annotated[AsyncConnectionPool, Depends(get_pool)],
) -> dict[str, object]:
    title = body.title.strip() or "New chat"
    async with pool.connection() as connection:
        cursor = await connection.execute(
            """
            INSERT INTO sessions (user_id, title)
            VALUES (%s, %s)
            RETURNING id, title, created_at, updated_at
            """,
            (user.id, title),
        )
        return await cursor.fetchone()


@router.patch("/{session_id}", response_model=SessionInfo)
async def rename_session(
    session_id: UUID,
    body: SessionRename,
    user: Annotated[CurrentUser, Depends(get_current_user)],
    pool: Annotated[AsyncConnectionPool, Depends(get_pool)],
) -> dict[str, object]:
    async with pool.connection() as connection:
        cursor = await connection.execute(
            """
            UPDATE sessions SET title = %s, updated_at = now()
            WHERE id = %s AND user_id = %s
            RETURNING id, title, created_at, updated_at
            """,
            (body.title.strip(), session_id, user.id),
        )
        session = await cursor.fetchone()
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found.")
    return session


@router.delete("/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_session(
    session_id: UUID,
    user: Annotated[CurrentUser, Depends(get_current_user)],
    pool: Annotated[AsyncConnectionPool, Depends(get_pool)],
) -> None:
    async with pool.connection() as connection:
        cursor = await connection.execute(
            "DELETE FROM sessions WHERE id = %s AND user_id = %s",
            (session_id, user.id),
        )
    if cursor.rowcount == 0:
        raise HTTPException(status_code=404, detail="Session not found.")

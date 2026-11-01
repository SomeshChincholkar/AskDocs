"""Message history and context-grounded streaming chat routes."""

import json
import logging
from collections.abc import AsyncIterator
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool

from app.config import Settings
from app.deps import (
    CurrentUser,
    enforce_rate_limit,
    get_app_settings,
    get_current_user,
    get_pool,
)
from app.schemas import AskRequest, MessageInfo
from app.services.llm import stream_answer
from app.services.retrieval import retrieve_chunks

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/sessions", tags=["chat"])


async def _ensure_owned_session(
    pool: AsyncConnectionPool,
    session_id: UUID,
    user_id: UUID,
) -> None:
    async with pool.connection() as connection:
        cursor = await connection.execute(
            "SELECT 1 FROM sessions WHERE id = %s AND user_id = %s",
            (session_id, user_id),
        )
        if await cursor.fetchone() is None:
            raise HTTPException(status_code=404, detail="Session not found.")


@router.get("/{session_id}/messages", response_model=list[MessageInfo])
async def list_messages(
    session_id: UUID,
    user: Annotated[CurrentUser, Depends(get_current_user)],
    pool: Annotated[AsyncConnectionPool, Depends(get_pool)],
) -> list[dict[str, object]]:
    await _ensure_owned_session(pool, session_id, user.id)
    async with pool.connection() as connection:
        cursor = await connection.execute(
            """
            SELECT messages.id, messages.role, messages.content, messages.sources,
                   messages.created_at
            FROM messages
            JOIN sessions ON sessions.id = messages.session_id
            WHERE messages.session_id = %s AND sessions.user_id = %s
            ORDER BY messages.created_at
            """,
            (session_id, user.id),
        )
        return list(await cursor.fetchall())


def _event(name: str, payload: object) -> str:
    return f"event: {name}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


async def _save_assistant(
    pool: AsyncConnectionPool,
    session_id: UUID,
    content: str,
    sources: list[dict[str, object]],
) -> None:
    async with pool.connection() as connection:
        await connection.execute(
            """
            INSERT INTO messages (session_id, role, content, sources)
            VALUES (%s, 'assistant', %s, %s)
            """,
            (session_id, content, Jsonb(sources)),
        )
        await connection.execute(
            "UPDATE sessions SET updated_at = now() WHERE id = %s",
            (session_id,),
        )


def _deduplicate_sources(
    chunks: list[dict[str, object]],
) -> list[dict[str, object]]:
    sources: list[dict[str, object]] = []
    seen: set[tuple[object, object]] = set()
    for chunk in chunks:
        key = (chunk["filename"], chunk["page"])
        if key in seen:
            continue
        seen.add(key)
        sources.append(
            {
                "filename": chunk["filename"],
                "page": chunk["page"],
                "snippet": str(chunk["content"])[:240],
            }
        )
    return sources


@router.post("/{session_id}/ask")
async def ask(
    request: Request,
    session_id: UUID,
    body: AskRequest,
    user: Annotated[CurrentUser, Depends(get_current_user)],
    pool: Annotated[AsyncConnectionPool, Depends(get_pool)],
    settings: Annotated[Settings, Depends(get_app_settings)],
) -> StreamingResponse:
    enforce_rate_limit(request, "questions", settings.rate_limit_questions_per_hour)
    question = body.question.strip()
    if not question:
        raise HTTPException(status_code=422, detail="Question cannot be blank.")
    await _ensure_owned_session(pool, session_id, user.id)

    async with pool.connection() as connection:
        ready_cursor = await connection.execute(
            """
            SELECT count(*) AS total FROM documents
            WHERE session_id = %s AND user_id = %s AND status = 'ready'
            """,
            (session_id, user.id),
        )
        has_documents = (await ready_cursor.fetchone())["total"] > 0
        count_cursor = await connection.execute(
            "SELECT count(*) AS total FROM messages WHERE session_id = %s",
            (session_id,),
        )
        is_first_question = (await count_cursor.fetchone())["total"] == 0
        user_cursor = await connection.execute(
            """
            INSERT INTO messages (session_id, role, content)
            VALUES (%s, 'user', %s)
            RETURNING id
            """,
            (session_id, question),
        )
        user_message_id = (await user_cursor.fetchone())["id"]
        history_cursor = await connection.execute(
            """
            SELECT role, content FROM messages
            WHERE session_id = %s AND id <> %s
            ORDER BY created_at DESC LIMIT 6
            """,
            (session_id, user_message_id),
        )
        history = list(reversed(await history_cursor.fetchall()))
        if is_first_question:
            title = question[:50].strip() or "New chat"
            await connection.execute(
                """
                UPDATE sessions SET title = %s, updated_at = now()
                WHERE id = %s AND user_id = %s
                """,
                (title, session_id, user.id),
            )
        else:
            await connection.execute(
                "UPDATE sessions SET updated_at = now() WHERE id = %s AND user_id = %s",
                (session_id, user.id),
            )

    async def stream() -> AsyncIterator[str]:
        if not has_documents:
            friendly = "Upload a document to this chat before asking a question."
            await _save_assistant(pool, session_id, friendly, [])
            yield _event("token", {"text": friendly})
            yield _event("sources", [])
            yield _event("done", {})
            return

        try:
            context = await retrieve_chunks(
                pool,
                settings,
                question,
                session_id,
                user.id,
            )
            accumulated: list[str] = []
            async for token in stream_answer(question, history, context, settings):
                accumulated.append(token)
                yield _event("token", {"text": token})
            answer = "".join(accumulated)
            sources = _deduplicate_sources(context)
            await _save_assistant(pool, session_id, answer, sources)
            yield _event("sources", sources)
            yield _event("done", {})
        except Exception:
            logger.exception("Question response failed for session %s.", session_id)
            yield _event(
                "error",
                {"message": "The answer could not be generated. Please try again."},
            )

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )

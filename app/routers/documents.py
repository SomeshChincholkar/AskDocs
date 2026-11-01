"""Document upload, status, and deletion routes."""

import mimetypes
from pathlib import Path
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, Request, UploadFile
from fastapi import status as http_status
from psycopg_pool import AsyncConnectionPool

from app.config import Settings
from app.deps import (
    CurrentUser,
    enforce_rate_limit,
    get_app_settings,
    get_current_user,
    get_pool,
)
from app.schemas import DocumentInfo, DocumentStatus
from app.services.ingestion import ingest_document

router = APIRouter(tags=["documents"])
ALLOWED_EXTENSIONS = {".pdf", ".docx", ".xlsx", ".xls", ".csv", ".txt", ".png", ".jpg", ".jpeg", ".webp"}


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


@router.get(
    "/api/sessions/{session_id}/documents",
    response_model=list[DocumentInfo],
)
async def list_documents(
    session_id: UUID,
    user: Annotated[CurrentUser, Depends(get_current_user)],
    pool: Annotated[AsyncConnectionPool, Depends(get_pool)],
) -> list[dict[str, object]]:
    await _ensure_owned_session(pool, session_id, user.id)
    async with pool.connection() as connection:
        cursor = await connection.execute(
            """
            SELECT id, filename, mime_type, size_bytes, page_count, chunk_count,
                   status, error, created_at
            FROM documents WHERE session_id = %s AND user_id = %s
            ORDER BY created_at
            """,
            (session_id, user.id),
        )
        return list(await cursor.fetchall())


@router.post(
    "/api/sessions/{session_id}/documents",
    status_code=http_status.HTTP_202_ACCEPTED,
)
async def upload_documents(
    request: Request,
    session_id: UUID,
    background_tasks: BackgroundTasks,
    files: Annotated[list[UploadFile], File()],
    user: Annotated[CurrentUser, Depends(get_current_user)],
    pool: Annotated[AsyncConnectionPool, Depends(get_pool)],
    settings: Annotated[Settings, Depends(get_app_settings)],
) -> dict[str, list[dict[str, str]]]:
    enforce_rate_limit(request, "uploads", settings.rate_limit_uploads_per_hour)
    if not files:
        raise HTTPException(status_code=400, detail="Select at least one file.")
    await _ensure_owned_session(pool, session_id, user.id)

    max_bytes = settings.max_upload_mb * 1024 * 1024
    prepared: list[tuple[str, str, bytes]] = []
    for upload in files:
        filename = Path(upload.filename or "").name.strip()
        extension = Path(filename).suffix.lower()
        if extension not in ALLOWED_EXTENSIONS:
            raise HTTPException(
                status_code=415,
                detail=f"Unsupported file type: {extension or 'unknown'}.",
            )
        content = await upload.read(max_bytes + 1)
        if len(content) > max_bytes:
            raise HTTPException(
                status_code=413,
                detail=f"{filename} exceeds the {settings.max_upload_mb} MB upload limit.",
            )
        mime_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
        prepared.append((filename, mime_type, content))

    documents: list[dict[str, str]] = []
    async with pool.connection() as connection:
        async with connection.transaction():
            session_cursor = await connection.execute(
                "SELECT id FROM sessions WHERE id = %s AND user_id = %s FOR UPDATE",
                (session_id, user.id),
            )
            if await session_cursor.fetchone() is None:
                raise HTTPException(status_code=404, detail="Session not found.")
            count_cursor = await connection.execute(
                "SELECT count(*) AS total FROM documents WHERE session_id = %s AND user_id = %s",
                (session_id, user.id),
            )
            existing_count = (await count_cursor.fetchone())["total"]
            if existing_count + len(files) > settings.max_files_per_session:
                raise HTTPException(
                    status_code=400,
                    detail=f"A session can contain at most {settings.max_files_per_session} files.",
                )
            for filename, mime_type, content in prepared:
                cursor = await connection.execute(
                    """
                    INSERT INTO documents
                        (session_id, user_id, filename, mime_type, size_bytes, status)
                    VALUES (%s, %s, %s, %s, %s, 'processing')
                    RETURNING id
                    """,
                    (session_id, user.id, filename, mime_type, len(content)),
                )
                document_id = (await cursor.fetchone())["id"]
                documents.append({"id": str(document_id), "filename": filename, "status": "processing"})
                background_tasks.add_task(
                    ingest_document,
                    pool,
                    settings,
                    document_id,
                    session_id,
                    user.id,
                    filename,
                    mime_type,
                    content,
                )
    return {"documents": documents}


@router.get("/api/documents/{document_id}/status", response_model=DocumentStatus)
async def document_status(
    document_id: UUID,
    user: Annotated[CurrentUser, Depends(get_current_user)],
    pool: Annotated[AsyncConnectionPool, Depends(get_pool)],
) -> dict[str, object]:
    async with pool.connection() as connection:
        cursor = await connection.execute(
            """
            SELECT status, chunk_count, error FROM documents
            WHERE id = %s AND user_id = %s
            """,
            (document_id, user.id),
        )
        document = await cursor.fetchone()
    if document is None:
        raise HTTPException(status_code=404, detail="Document not found.")
    return document


@router.delete("/api/documents/{document_id}", status_code=http_status.HTTP_204_NO_CONTENT)
async def delete_document(
    document_id: UUID,
    user: Annotated[CurrentUser, Depends(get_current_user)],
    pool: Annotated[AsyncConnectionPool, Depends(get_pool)],
) -> None:
    async with pool.connection() as connection:
        cursor = await connection.execute(
            "DELETE FROM documents WHERE id = %s AND user_id = %s",
            (document_id, user.id),
        )
    if cursor.rowcount == 0:
        raise HTTPException(status_code=404, detail="Document not found.")

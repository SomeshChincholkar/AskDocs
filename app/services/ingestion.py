"""Document extraction, chunk embedding, and transactional persistence."""

import logging
from uuid import UUID

from pgvector import Vector
from psycopg_pool import AsyncConnectionPool

from app.config import Settings
from app.services.chunking import chunk_pages
from app.services.embeddings import embed_documents
from app.services.extraction import ExtractionError, extract_document

logger = logging.getLogger(__name__)
NO_TEXT_ERROR = "No readable text found"
PROCESSING_ERROR = "Could not process this document. Please check the file and try again."


async def ingest_document(
    pool: AsyncConnectionPool,
    settings: Settings,
    document_id: UUID,
    session_id: UUID,
    user_id: UUID,
    filename: str,
    mime_type: str,
    data: bytes,
) -> None:
    """Run ingestion and always transition a document out of processing."""
    try:
        pages = await extract_document(data, filename, mime_type, settings)
        if not any(text.strip() for _, text in pages):
            await _mark_failed(pool, document_id, user_id, NO_TEXT_ERROR)
            return

        chunks = chunk_pages(pages, settings.chunk_size, settings.chunk_overlap)
        if not chunks:
            await _mark_failed(pool, document_id, user_id, NO_TEXT_ERROR)
            return
        vectors = await embed_documents([content for _, content in chunks], settings)

        async with pool.connection() as connection:
            async with connection.transaction():
                async with connection.cursor() as cursor:
                    await cursor.executemany(
                        """
                        INSERT INTO chunks
                            (document_id, session_id, user_id, content, page, chunk_index, embedding)
                        VALUES (%s, %s, %s, %s, %s, %s, %s)
                        """,
                        [
                            (
                                document_id,
                                session_id,
                                user_id,
                                content,
                                page,
                                index,
                                Vector(vector),
                            )
                            for index, ((page, content), vector) in enumerate(
                                zip(chunks, vectors, strict=True)
                            )
                        ],
                    )
                await connection.execute(
                    """
                    UPDATE documents
                    SET page_count = %s, chunk_count = %s, status = 'ready', error = NULL
                    WHERE id = %s AND user_id = %s
                    """,
                    (len(pages), len(chunks), document_id, user_id),
                )
    except ExtractionError as error:
        logger.info("Document %s exceeded extraction limits: %s", document_id, error)
        await _mark_failed(pool, document_id, user_id, str(error)[:200])
    except Exception:
        logger.exception("Document ingestion failed for document %s.", document_id)
        try:
            await _mark_failed(pool, document_id, user_id, PROCESSING_ERROR)
        except Exception:
            logger.exception("Unable to mark document %s as failed.", document_id)
    finally:
        del data


async def _mark_failed(
    pool: AsyncConnectionPool,
    document_id: UUID,
    user_id: UUID,
    error: str,
) -> None:
    async with pool.connection() as connection:
        await connection.execute(
            """
            UPDATE documents
            SET status = 'failed', error = %s
            WHERE id = %s AND user_id = %s AND status = 'processing'
            """,
            (error, document_id, user_id),
        )

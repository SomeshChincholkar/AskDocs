"""Session- and user-scoped pgvector similarity search."""

from uuid import UUID

from pgvector import Vector
from psycopg_pool import AsyncConnectionPool

from app.config import Settings
from app.services.embeddings import embed_query


async def retrieve_chunks(
    pool: AsyncConnectionPool,
    settings: Settings,
    question: str,
    session_id: UUID,
    user_id: UUID,
) -> list[dict[str, object]]:
    """Return the closest ready-document chunks for one user's session."""
    vector = await embed_query(question, settings)
    async with pool.connection() as connection:
        cursor = await connection.execute(
            """
            SELECT chunks.content, chunks.page, chunks.document_id, documents.filename,
                   1 - (chunks.embedding <=> %s) AS score
            FROM chunks
            JOIN documents ON documents.id = chunks.document_id
            WHERE chunks.session_id = %s
              AND chunks.user_id = %s
              AND documents.user_id = %s
              AND documents.status = 'ready'
            ORDER BY chunks.embedding <=> %s
            LIMIT %s
            """,
            (
                Vector(vector),
                session_id,
                user_id,
                user_id,
                Vector(vector),
                settings.top_k,
            ),
        )
        return list(await cursor.fetchall())

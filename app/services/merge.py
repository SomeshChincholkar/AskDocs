"""Move anonymous ownership to an existing registered account."""

import logging
from uuid import UUID

from psycopg_pool import AsyncConnectionPool

logger = logging.getLogger(__name__)


async def merge_anonymous_data(
    pool: AsyncConnectionPool,
    anonymous_user_id: UUID,
    account_id: UUID,
) -> dict[str, int]:
    """Atomically move anonymous sessions, documents, and chunks to an account."""
    async with pool.connection() as connection:
        async with connection.transaction():
            cursor = await connection.execute(
                """
                SELECT id FROM users
                WHERE id = %s AND is_anonymous = true AND username IS NULL
                FOR UPDATE
                """,
                (anonymous_user_id,),
            )
            anonymous_user = await cursor.fetchone()
            if anonymous_user is None or anonymous_user_id == account_id:
                return {"sessions": 0, "documents": 0}

            sessions_cursor = await connection.execute(
                "UPDATE sessions SET user_id = %s WHERE user_id = %s",
                (account_id, anonymous_user_id),
            )
            documents_cursor = await connection.execute(
                "UPDATE documents SET user_id = %s WHERE user_id = %s",
                (account_id, anonymous_user_id),
            )
            await connection.execute(
                "UPDATE chunks SET user_id = %s WHERE user_id = %s",
                (account_id, anonymous_user_id),
            )
            await connection.execute(
                "DELETE FROM users WHERE id = %s AND is_anonymous = true AND username IS NULL",
                (anonymous_user_id,),
            )
            return {
                "sessions": sessions_cursor.rowcount,
                "documents": documents_cursor.rowcount,
            }

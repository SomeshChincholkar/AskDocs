"""PostgreSQL connection pool and helpers."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from psycopg import AsyncConnection
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool
from pgvector.psycopg import register_vector_async

from app.config import Settings


async def _configure_connection(connection: AsyncConnection) -> None:
    await register_vector_async(connection)


def create_pool(settings: Settings) -> AsyncConnectionPool | None:
    """Create a lazy pool when a database URL is configured."""
    if not settings.database_url:
        return None
    return AsyncConnectionPool(
        conninfo=settings.database_url,
        min_size=1,
        max_size=10,
        open=False,
        kwargs={"row_factory": dict_row},
        configure=_configure_connection,
    )


@asynccontextmanager
async def get_connection(pool: AsyncConnectionPool) -> AsyncIterator[AsyncConnection]:
    """Borrow a configured connection from the application pool."""
    async with pool.connection() as connection:
        yield connection

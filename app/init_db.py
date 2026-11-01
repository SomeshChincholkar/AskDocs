"""Apply sql/schema.sql to the configured PostgreSQL database."""

import asyncio
import logging
from pathlib import Path
import sys

from app.config import get_settings
from app.db import create_pool

logger = logging.getLogger(__name__)


async def main() -> None:
    settings = get_settings()
    pool = create_pool(settings)
    if pool is None:
        raise RuntimeError("DATABASE_URL must be set before initializing the database.")
    await pool.open()
    try:
        schema = Path(__file__).resolve().parent.parent / "sql" / "schema.sql"
        async with pool.connection() as connection:
            for statement in schema.read_text(encoding="utf-8").split(";"):
                if statement.strip():
                    await connection.execute(statement)
        logger.info("Database schema initialized.")
    finally:
        await pool.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())

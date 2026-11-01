"""Delete anonymous users that have been inactive for over 90 days."""

import asyncio
import logging
import sys

from app.config import get_settings
from app.db import create_pool

logger = logging.getLogger(__name__)


async def main() -> None:
    """Run the anonymous-user retention cleanup."""
    pool = create_pool(get_settings())
    if pool is None:
        raise RuntimeError("DATABASE_URL must be set before running cleanup.")
    await pool.open(wait=True)
    try:
        async with pool.connection() as connection:
            cursor = await connection.execute(
                """
                DELETE FROM users
                WHERE is_anonymous = true
                  AND last_seen_at < now() - interval '90 days'
                """
            )
            logger.info("Deleted %s inactive anonymous users.", cursor.rowcount)
    finally:
        await pool.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())

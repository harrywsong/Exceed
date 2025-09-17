# utils/database.py
import asyncpg
import os
from typing import Dict, Any, Optional

# 환경 변수에서 데이터베이스 URL 가져오기
DATABASE_URL = os.getenv("DATABASE_URL")

class Database:
    """
    Handles database connection and queries.
    """
    _pool: Optional[asyncpg.Pool] = None

    @classmethod
    async def get_pool(cls) -> asyncpg.Pool:
        """Get or create the database connection pool."""
        if cls._pool is None:
            if not DATABASE_URL:
                raise ValueError("DATABASE_URL environment variable is not set.")
            cls._pool = await asyncpg.create_pool(dsn=DATABASE_URL)
        return cls._pool

    @classmethod
    async def get_user_stats(cls, user_id: int, guild_id: int, category: str) -> Dict[str, Any]:
        """
        Retrieves user statistics from the database.
        Note: The database schema is assumed to have a 'user_stats' table
        that stores casino game data.
        """
        pool = await cls.get_pool()
        async with pool.acquire() as conn:
            # Assumes a table named 'casino_stats' exists
            # with columns: user_id, guild_id, game_type, games_played, total_bets, total_wins
            query = """
                SELECT
                    game_type,
                    games_played,
                    total_bets,
                    total_wins
                FROM casino_stats
                WHERE user_id = $1 AND guild_id = $2
            """
            rows = await conn.fetch(query, user_id, guild_id)

            if not rows:
                return {}

            stats = {}
            for row in rows:
                stats[row['game_type']] = {
                    "games": row['games_played'],
                    "bets": row['total_bets'],
                    "wins": row['total_wins']
                }
            return stats
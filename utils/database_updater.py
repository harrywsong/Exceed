# utils/database_updater.py
import asyncio
import asyncpg
import logging
from pathlib import Path


class DatabaseUpdater:
    def __init__(self, pool):
        self.pool = pool
        self.logger = logging.getLogger('database_updater')

    async def update_database_schema(self):
        """Update database schema to support multi-server architecture"""
        async with self.pool.acquire() as conn:
            try:
                self.logger.info("Starting database schema update...")

                # Create migrations tracking table
                await conn.execute("""
                    CREATE TABLE IF NOT EXISTS schema_migrations (
                        version VARCHAR(255) PRIMARY KEY,
                        applied_at TIMESTAMP DEFAULT NOW()
                    )
                """)

                # Check if migration was already applied
                migration_version = "001_add_guild_id_columns"
                existing = await conn.fetchrow(
                    "SELECT version FROM schema_migrations WHERE version = $1",
                    migration_version
                )

                if existing:
                    self.logger.info("Database schema is already up to date")
                    return

                # Apply the migration
                await self._apply_guild_id_migration(conn)

                # Mark migration as applied
                await conn.execute(
                    "INSERT INTO schema_migrations (version) VALUES ($1)",
                    migration_version
                )

                self.logger.info("Database schema updated successfully")

            except Exception as e:
                self.logger.error(f"Database schema update failed: {e}", exc_info=True)
                raise

    async def _apply_guild_id_migration(self, conn):
        """Apply the guild_id column migration"""

        # List of tables that need guild_id column
        tables_to_update = [
            'user_coins',
            'user_registration',
            'user_achievements',
            'casino_stats',
            'user_voice_stats',
            'message_history'
        ]

        for table_name in tables_to_update:
            try:
                # Check if table exists
                table_exists = await conn.fetchrow("""
                    SELECT EXISTS (
                        SELECT FROM information_schema.tables 
                        WHERE table_name = $1
                    )
                """, table_name)

                if not table_exists[0]:
                    self.logger.info(f"Table {table_name} doesn't exist, skipping...")
                    continue

                # Check if guild_id column exists
                column_exists = await conn.fetchrow("""
                    SELECT EXISTS (
                        SELECT FROM information_schema.columns 
                        WHERE table_name = $1 AND column_name = 'guild_id'
                    )
                """, table_name)

                if not column_exists[0]:
                    self.logger.info(f"Adding guild_id column to {table_name}...")

                    # Add guild_id column
                    await conn.execute(f"""
                        ALTER TABLE {table_name} 
                        ADD COLUMN guild_id BIGINT NOT NULL DEFAULT 0
                    """)

                    # Create indexes for better performance
                    await conn.execute(f"""
                        CREATE INDEX IF NOT EXISTS idx_{table_name}_guild_id 
                        ON {table_name}(guild_id)
                    """)

                    # Create composite index for user-related tables
                    if 'user' in table_name.lower() or table_name in ['casino_stats']:
                        await conn.execute(f"""
                            CREATE INDEX IF NOT EXISTS idx_{table_name}_guild_user 
                            ON {table_name}(guild_id, user_id)
                        """)

                    self.logger.info(f"Successfully added guild_id to {table_name}")
                else:
                    self.logger.info(f"Table {table_name} already has guild_id column")

            except Exception as e:
                self.logger.error(f"Failed to update table {table_name}: {e}")
                # Continue with other tables instead of failing completely

    async def clean_legacy_data(self, guild_id: int):
        """Clean up data that doesn't belong to the specified guild"""
        async with self.pool.acquire() as conn:
            try:
                self.logger.info(f"Cleaning legacy data for guild {guild_id}...")

                # Update records with guild_id = 0 to the current guild
                # This assumes all existing data belongs to the first configured guild

                tables_to_clean = [
                    'user_coins',
                    'user_registration',
                    'user_achievements',
                    'casino_stats',
                    'user_voice_stats',
                    'message_history'
                ]

                for table_name in tables_to_clean:
                    try:
                        # Check if table exists and has guild_id column
                        table_info = await conn.fetchrow("""
                            SELECT EXISTS (
                                SELECT FROM information_schema.columns 
                                WHERE table_name = $1 AND column_name = 'guild_id'
                            )
                        """, table_name)

                        if table_info[0]:
                            # Update records with guild_id = 0
                            result = await conn.execute(f"""
                                UPDATE {table_name} 
                                SET guild_id = $1 
                                WHERE guild_id = 0
                            """, guild_id)

                            rows_affected = int(result.split()[-1])
                            if rows_affected > 0:
                                self.logger.info(f"Updated {rows_affected} rows in {table_name}")

                    except Exception as e:
                        self.logger.error(f"Failed to clean table {table_name}: {e}")

                self.logger.info(f"Legacy data cleanup completed for guild {guild_id}")

            except Exception as e:
                self.logger.error(f"Legacy data cleanup failed: {e}", exc_info=True)
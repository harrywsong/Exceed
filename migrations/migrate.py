# migrations/migrate.py
import asyncio
import asyncpg
import logging
import os
from pathlib import Path


# Database migration system
class DatabaseMigrator:
    def __init__(self, database_url: str):
        self.database_url = database_url
        self.migrations_dir = Path(__file__).parent / "sql"
        self.logger = logging.getLogger(__name__)

    async def run_migrations(self):
        """Run all pending migrations"""
        conn = await asyncpg.connect(self.database_url)

        try:
            # Create migrations table if it doesn't exist
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version VARCHAR(255) PRIMARY KEY,
                    applied_at TIMESTAMP DEFAULT NOW()
                )
            """)

            # Get applied migrations
            applied = await conn.fetch("SELECT version FROM schema_migrations ORDER BY version")
            applied_versions = {row['version'] for row in applied}

            # Get available migrations
            migration_files = sorted(self.migrations_dir.glob("*.sql"))

            for migration_file in migration_files:
                version = migration_file.stem

                if version not in applied_versions:
                    self.logger.info(f"Applying migration: {version}")

                    # Read and execute migration
                    with open(migration_file, 'r') as f:
                        migration_sql = f.read()

                    async with conn.transaction():
                        await conn.execute(migration_sql)
                        await conn.execute(
                            "INSERT INTO schema_migrations (version) VALUES ($1)",
                            version
                        )

                    self.logger.info(f"Migration {version} applied successfully")
                else:
                    self.logger.debug(f"Migration {version} already applied")

        except Exception as e:
            self.logger.error(f"Migration failed: {e}")
            raise
        finally:
            await conn.close()


async def main():
    """Run migrations from command line"""
    import sys
    if len(sys.argv) != 2:
        print("Usage: python migrate.py <DATABASE_URL>")
        sys.exit(1)

    database_url = sys.argv[1]
    migrator = DatabaseMigrator(database_url)

    try:
        await migrator.run_migrations()
        print("All migrations completed successfully")
    except Exception as e:
        print(f"Migration failed: {e}")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
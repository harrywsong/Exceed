-- migrations/sql/002_add_missing_tables.sql
-- Add guild_id to registration tables and other missing tables

-- Create user_registration table if it doesn't exist
CREATE TABLE IF NOT EXISTS user_registration (
    user_id BIGINT NOT NULL,
    guild_id BIGINT NOT NULL DEFAULT 0,
    registered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (user_id, guild_id)
);

-- Add guild_id column to user_registration if table exists but column doesn't
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.tables
        WHERE table_name = 'user_registration'
    ) AND NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'user_registration' AND column_name = 'guild_id'
    ) THEN
        ALTER TABLE user_registration ADD COLUMN guild_id BIGINT NOT NULL DEFAULT 0;
        CREATE INDEX IF NOT EXISTS idx_user_registration_guild_id ON user_registration(guild_id);
        CREATE INDEX IF NOT EXISTS idx_user_registration_guild_user ON user_registration(guild_id, user_id);
    END IF;
END
$$;

-- Ensure user_coins table has all necessary columns
DO $$
BEGIN
    -- Add guild_id if missing (should already exist from previous migration)
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'user_coins' AND column_name = 'guild_id'
    ) THEN
        ALTER TABLE user_coins ADD COLUMN guild_id BIGINT NOT NULL DEFAULT 0;
        CREATE INDEX IF NOT EXISTS idx_user_coins_guild_id ON user_coins(guild_id);
        CREATE INDEX IF NOT EXISTS idx_user_coins_guild_user ON user_coins(guild_id, user_id);
    END IF;
END
$$;

-- Create any other missing tables that your cogs might need
CREATE TABLE IF NOT EXISTS user_achievements (
    user_id BIGINT NOT NULL,
    guild_id BIGINT NOT NULL DEFAULT 0,
    achievement_name VARCHAR(255) NOT NULL,
    unlocked_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (user_id, guild_id, achievement_name)
);

CREATE INDEX IF NOT EXISTS idx_user_achievements_guild_id ON user_achievements(guild_id);
CREATE INDEX IF NOT EXISTS idx_user_achievements_guild_user ON user_achievements(guild_id, user_id);
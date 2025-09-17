-- migrations/sql/001_add_guild_id_columns.sql
-- Add guild_id columns to existing tables for multi-server support

-- Add guild_id to user_coins table
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'user_coins' AND column_name = 'guild_id'
    ) THEN
        ALTER TABLE user_coins ADD COLUMN guild_id BIGINT NOT NULL DEFAULT 0;

        -- Create index for better performance
        CREATE INDEX IF NOT EXISTS idx_user_coins_guild_id ON user_coins(guild_id);
        CREATE INDEX IF NOT EXISTS idx_user_coins_guild_user ON user_coins(guild_id, user_id);
    END IF;
END
$$;

-- Add guild_id to user_registration table
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'user_registration' AND column_name = 'guild_id'
    ) THEN
        ALTER TABLE user_registration ADD COLUMN guild_id BIGINT NOT NULL DEFAULT 0;

        -- Create index for better performance
        CREATE INDEX IF NOT EXISTS idx_user_registration_guild_id ON user_registration(guild_id);
        CREATE INDEX IF NOT EXISTS idx_user_registration_guild_user ON user_registration(guild_id, user_id);
    END IF;
END
$$;

-- Add guild_id to user_achievements table (if exists)
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.tables
        WHERE table_name = 'user_achievements'
    ) AND NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'user_achievements' AND column_name = 'guild_id'
    ) THEN
        ALTER TABLE user_achievements ADD COLUMN guild_id BIGINT NOT NULL DEFAULT 0;

        -- Create index for better performance
        CREATE INDEX IF NOT EXISTS idx_user_achievements_guild_id ON user_achievements(guild_id);
        CREATE INDEX IF NOT EXISTS idx_user_achievements_guild_user ON user_achievements(guild_id, user_id);
    END IF;
END
$$;

-- Add guild_id to casino_stats table (if exists)
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.tables
        WHERE table_name = 'casino_stats'
    ) AND NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'casino_stats' AND column_name = 'guild_id'
    ) THEN
        ALTER TABLE casino_stats ADD COLUMN guild_id BIGINT NOT NULL DEFAULT 0;

        -- Create index for better performance
        CREATE INDEX IF NOT EXISTS idx_casino_stats_guild_id ON casino_stats(guild_id);
        CREATE INDEX IF NOT EXISTS idx_casino_stats_guild_user ON casino_stats(guild_id, user_id);
    END IF;
END
$$;

-- Add guild_id to user_voice_stats table (if exists)
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.tables
        WHERE table_name = 'user_voice_stats'
    ) AND NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'user_voice_stats' AND column_name = 'guild_id'
    ) THEN
        ALTER TABLE user_voice_stats ADD COLUMN guild_id BIGINT NOT NULL DEFAULT 0;

        -- Create index for better performance
        CREATE INDEX IF NOT EXISTS idx_user_voice_stats_guild_id ON user_voice_stats(guild_id);
        CREATE INDEX IF NOT EXISTS idx_user_voice_stats_guild_user ON user_voice_stats(guild_id, user_id);
    END IF;
END
$$;

-- Add guild_id to message_history table (if exists)
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.tables
        WHERE table_name = 'message_history'
    ) AND NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'message_history' AND column_name = 'guild_id'
    ) THEN
        ALTER TABLE message_history ADD COLUMN guild_id BIGINT NOT NULL DEFAULT 0;

        -- Create index for better performance
        CREATE INDEX IF NOT EXISTS idx_message_history_guild_id ON message_history(guild_id);
    END IF;
END
$$;

-- Update reaction_roles_table to ensure guild_id exists (it should already)
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'reaction_roles_table' AND column_name = 'guild_id'
    ) THEN
        ALTER TABLE reaction_roles_table ADD COLUMN guild_id BIGINT NOT NULL DEFAULT 0;

        -- Create index for better performance
        CREATE INDEX IF NOT EXISTS idx_reaction_roles_guild_id ON reaction_roles_table(guild_id);
    END IF;
END
$$;

-- Create missing tables that cogs expect to exist
CREATE TABLE IF NOT EXISTS user_registration (
    user_id BIGINT NOT NULL,
    guild_id BIGINT NOT NULL DEFAULT 0,
    registered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    verification_code VARCHAR(50),
    is_verified BOOLEAN DEFAULT FALSE,
    PRIMARY KEY (user_id, guild_id)
);

CREATE INDEX IF NOT EXISTS idx_user_registration_guild_id ON user_registration(guild_id);
CREATE INDEX IF NOT EXISTS idx_user_registration_guild_user ON user_registration(guild_id, user_id);
# migrate_to_multiserver.py
"""
Migration script to convert single-server .env configuration
to multi-server configuration system.

Usage: python migrate_to_multiserver.py
"""

import os
import json
from dotenv import load_dotenv
from utils.config import save_server_config


def migrate_legacy_config():
    """Migrate existing .env configuration to multi-server format"""

    # Load the .env file
    load_dotenv()

    # Get the guild ID from .env
    guild_id = os.getenv("GUILD_ID")
    if not guild_id:
        print("❌ GUILD_ID not found in .env file. Cannot migrate.")
        return False

    try:
        guild_id = int(guild_id)
    except ValueError:
        print("❌ Invalid GUILD_ID format in .env file.")
        return False

    print(f"🔄 Migrating configuration for Guild ID: {guild_id}")

    # Create the new server configuration
    server_config = {
        'guild_id': str(guild_id),
        'guild_name': 'Migrated Server (Update with /bot-status)',
        'channels': {},
        'roles': {},
        'features': {
            'welcome_messages': True,
            'achievements': True,
            'ticket_system': True,
            'voice_channels': True,
            'casino_games': True,
            'message_history': True,
            'reaction_roles': True,
            'auto_moderation': False,
        },
        'settings': {
            'starting_coins': 1000
        }
    }

    # Channel mappings from old to new format
    channel_mappings = {
        'LOG_CHANNEL_ID': 'log_channel',
        'WELCOME_CHANNEL_ID': 'welcome_channel',
        'GOODBYE_CHANNEL_ID': 'goodbye_channel',
        'MEMBER_CHAT_CHANNEL_ID': 'member_chat_channel',
        'MESSAGE_HISTORY_CHANNEL_ID': 'message_history_channel',
        'ACHIEVEMENT_CHANNEL_ID': 'achievement_channel',
        'LEADERBOARD_CHANNEL_ID': 'leaderboard_channel',
        'TICKET_CHANNEL_ID': 'ticket_channel',
        'LOBBY_VOICE_CHANNEL_ID': 'lobby_voice',
        'ANNOUNCEMENTS_CHANNEL_ID': 'announcements_channel',
        'RULES_CHANNEL_ID': 'rules_channel',
        'ROLE_ASSIGN_CHANNEL_ID': 'role_assign_channel',
        'HISTORY_CHANNEL_ID': 'history_channel',
        'INTERVIEW_PUBLIC_CHANNEL_ID': 'interview_public_channel',
        'INTERVIEW_PRIVATE_CHANNEL_ID': 'interview_private_channel',
        'CLAN_LEADERBOARD_CHANNEL_ID': 'clan_leaderboard_channel',
    }

    # Migrate channels
    migrated_channels = 0
    for env_key, config_key in channel_mappings.items():
        channel_id = os.getenv(env_key)
        if channel_id and channel_id != '0':
            try:
                channel_id_int = int(channel_id)
                server_config['channels'][config_key] = {
                    'id': channel_id_int,
                    'name': 'Migrated Channel (Update name with /bot-setup)'
                }
                print(f"✅ Migrated {env_key} -> {config_key}")
                migrated_channels += 1
            except ValueError:
                print(f"⚠️ Invalid channel ID for {env_key}: {channel_id}")

    # Role mappings from old to new format
    role_mappings = {
        'STAFF_ROLE_ID': 'staff_role',
        'ADMIN_ROLE_ID': 'admin_role',
        'MEMBER_ROLE': 'member_role',
        'UNVERIFIED_ROLE_ID': 'unverified_role',
        'ACCEPTED_ROLE_ID': 'accepted_role',
        'APPLICANT_ROLE_ID': 'applicant_role',
        'GUEST_ROLE_ID': 'guest_role',
    }

    # Migrate roles
    migrated_roles = 0
    for env_key, config_key in role_mappings.items():
        role_id = os.getenv(env_key)
        if role_id and role_id != '0':
            try:
                role_id_int = int(role_id)
                server_config['roles'][config_key] = {
                    'id': role_id_int,
                    'name': 'Migrated Role (Update name with /bot-setup)'
                }
                print(f"✅ Migrated {env_key} -> {config_key}")
                migrated_roles += 1
            except ValueError:
                print(f"⚠️ Invalid role ID for {env_key}: {role_id}")

    # Handle special configurations
    temp_voice_category = os.getenv("TEMP_VOICE_CATEGORY_ID")
    if temp_voice_category and temp_voice_category != '0':
        try:
            server_config['channels']['temp_voice_category'] = {
                'id': int(temp_voice_category),
                'name': 'Temp Voice Category'
            }
            print("✅ Migrated TEMP_VOICE_CATEGORY_ID")
        except ValueError:
            print("⚠️ Invalid TEMP_VOICE_CATEGORY_ID")

    ticket_category = os.getenv("TICKET_CATEGORY_ID")
    if ticket_category and ticket_category != '0':
        try:
            server_config['channels']['ticket_category'] = {
                'id': int(ticket_category),
                'name': 'Ticket Category'
            }
            print("✅ Migrated TICKET_CATEGORY_ID")
        except ValueError:
            print("⚠️ Invalid TICKET_CATEGORY_ID")

    # Handle reaction roles if they exist
    reaction_role_json = os.getenv("REACTION_ROLE_MAP_JSON")
    if reaction_role_json and reaction_role_json != "{}":
        try:
            reaction_roles = json.loads(reaction_role_json)
            server_config['reaction_roles'] = reaction_roles
            print(f"✅ Migrated reaction roles: {len(reaction_roles)} message mappings")
        except json.JSONDecodeError as e:
            print(f"⚠️ Failed to parse REACTION_ROLE_MAP_JSON: {e}")

    # Save the new configuration
    success = save_server_config(guild_id, server_config)

    if success:
        print(f"\n🎉 Migration completed successfully!")
        print(f"📊 Summary:")
        print(f"   • Guild ID: {guild_id}")
        print(f"   • Channels migrated: {migrated_channels}")
        print(f"   • Roles migrated: {migrated_roles}")
        print(f"   • Features enabled: {len([f for f in server_config['features'].values() if f])}")

        print(f"\n📝 Next steps:")
        print(f"   1. Run your bot with the new multi-server configuration")
        print(f"   2. Use `/bot-status` to verify the configuration")
        print(f"   3. Use `/bot-setup` to update channel/role names and add missing configs")
        print(f"   4. Test all features to ensure they work correctly")

        print(f"\n💡 Tips:")
        print(f"   • The old .env file is still intact - this is non-destructive")
        print(f"   • You can run `/bot-setup` anytime to reconfigure")
        print(f"   • Check data/server_configs.json to see the new format")

        return True
    else:
        print("❌ Failed to save migrated configuration")
        return False


def backup_env_file():
    """Create a backup of the current .env file"""
    if os.path.exists('.env'):
        import shutil
        from datetime import datetime
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_name = f'.env.backup_{timestamp}'
        shutil.copy('.env', backup_name)
        print(f"📄 Created backup: {backup_name}")
        return True
    return False


def main():
    """Main migration function"""
    print("🔄 Exceed Bot Multi-Server Migration Tool")
    print("=" * 50)

    # Check if .env exists
    if not os.path.exists('.env'):
        print("❌ No .env file found. Nothing to migrate.")
        return

    # Check if migration has already been done
    if os.path.exists('data/server_configs.json'):
        response = input("⚠️ Server configurations already exist. Continue anyway? (y/N): ")
        if response.lower() not in ['y', 'yes']:
            print("Migration cancelled.")
            return

    # Create backup
    print("\n📄 Creating backup...")
    backup_env_file()

    # Run migration
    print("\n🔄 Starting migration...")
    if migrate_legacy_config():
        print("\n✅ Migration completed successfully!")
    else:
        print("\n❌ Migration failed!")


if __name__ == "__main__":
    main()
# utils/config.py
import os
import json
from dotenv import load_dotenv
from typing import Optional, Dict, Any, Union

# Load environment variables
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(BASE_DIR, '.env'))


# =============================================================================
# UTILITY FUNCTIONS
# =============================================================================

def parse_int(env_var_name, default=None):
    """Parse integer from environment variable"""
    val = os.getenv(env_var_name)
    try:
        return int(val) if val is not None else default
    except ValueError:
        return default


def parse_ids(env_var):
    """Parse comma-separated IDs from environment variable"""
    raw = os.getenv(env_var, "")
    return [int(x) for x in raw.split(",") if x.strip().isdigit()]


# =============================================================================
# GLOBAL CONFIGURATION (from .env)
# =============================================================================

def get_global_config() -> Dict[str, Any]:
    """Get global bot configuration from .env"""
    return {
        'DISCORD_TOKEN': os.getenv("DISCORD_TOKEN"),
        'BOT_ID': os.getenv("BOT_ID", "0"),
        'DATABASE_URL': os.getenv("DATABASE_URL"),
        'GSHEET_CREDENTIALS_PATH': os.getenv("GSHEET_CREDENTIALS_PATH", "exceed-465801-9a237edcd3b1.json"),
        'COMMAND_PREFIX': os.getenv("COMMAND_PREFIX", "!"),
    }


# Global constants that apply to all servers
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
BOT_ID = os.getenv("BOT_ID", "0")
DATABASE_URL = os.getenv("DATABASE_URL")
GSHEET_CREDENTIALS_PATH = os.getenv("GSHEET_CREDENTIALS_PATH", "exceed-465801-9a237edcd3b1.json")
COMMAND_PREFIX = os.getenv("COMMAND_PREFIX", "!")

# Achievement system constants
ACHIEVEMENT_DATA_PATH = "data/achievements.json"
ACHIEVEMENT_EMOJIS = {
    "general": "🏆",
    "hidden": "🤫",
}

HOLIDAYS = [
    "january 1",  # New Year's Day (Canada & USA)
    "february 14",  # Valentine's Day (Canada & USA)
    "february 19",  # Family Day (Canada - 3rd Monday in Feb, varies by province)
    "february 19",  # Presidents' Day (USA - 3rd Monday in Feb)
    "march 17",  # St. Patrick's Day (Canada & USA)
    "april 1",  # April Fools' Day (Canada & USA)
    "july 1",  # Canada Day (Canada)
    "july 4",  # Independence Day (USA)
    "september 2",  # Labour Day (Canada & USA - 1st Monday in Sept)
    "october 14",  # Thanksgiving (Canada - 2nd Monday in Oct)
    "october 31",  # Halloween (Canada & USA)
    "november 11",  # Remembrance Day (Canada)
    "november 28",  # Thanksgiving (USA - 4th Thursday in Nov)
    "december 25",  # Christmas (Canada & USA)
    "december 26",  # Boxing Day (Canada)
]


# =============================================================================
# SERVER-SPECIFIC CONFIGURATION FUNCTIONS
# =============================================================================

def load_server_config(guild_id: int) -> Dict[str, Any]:
    """Load configuration for a specific server"""
    try:
        config_path = os.path.join(BASE_DIR, 'data', 'server_configs.json')
        if os.path.exists(config_path):
            with open(config_path, 'r', encoding='utf-8') as f:
                all_configs = json.load(f)
            return all_configs.get(str(guild_id), {})
        return {}
    except Exception as e:
        print(f"Error loading server config for {guild_id}: {e}")
        return {}


def save_server_config(guild_id: int, config: Dict[str, Any]) -> bool:
    """Save configuration for a specific server"""
    try:
        config_path = os.path.join(BASE_DIR, 'data', 'server_configs.json')

        # Ensure data directory exists
        os.makedirs(os.path.dirname(config_path), exist_ok=True)

        # Load existing configs
        all_configs = {}
        if os.path.exists(config_path):
            with open(config_path, 'r', encoding='utf-8') as f:
                all_configs = json.load(f)

        # Update config for this server
        all_configs[str(guild_id)] = config

        # Save back to file
        with open(config_path, 'w', encoding='utf-8') as f:
            json.dump(all_configs, f, indent=2, ensure_ascii=False)

        return True
    except Exception as e:
        print(f"Error saving server config for {guild_id}: {e}")
        return False


def get_all_server_configs() -> Dict[str, Dict[str, Any]]:
    """Get all server configurations"""
    try:
        config_path = os.path.join(BASE_DIR, 'data', 'server_configs.json')
        if os.path.exists(config_path):
            with open(config_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        return {}
    except Exception as e:
        print(f"Error loading all server configs: {e}")
        return {}


# =============================================================================
# HELPER FUNCTIONS FOR ACCESSING SERVER-SPECIFIC DATA
# =============================================================================

def get_channel_id(guild_id: int, channel_key: str) -> int:
    """Get channel ID for a specific server and channel type"""
    config = load_server_config(guild_id)
    channel_data = config.get('channels', {}).get(channel_key)
    if channel_data and isinstance(channel_data, dict):
        return channel_data.get('id', 0)
    return 0


def get_channel_name(guild_id: int, channel_key: str) -> str:
    """Get channel name for a specific server and channel type"""
    config = load_server_config(guild_id)
    channel_data = config.get('channels', {}).get(channel_key)
    if channel_data and isinstance(channel_data, dict):
        return channel_data.get('name', 'Unknown')
    return 'Unknown'


def get_role_id(guild_id: int, role_key: str) -> int:
    """Get role ID for a specific server and role type"""
    config = load_server_config(guild_id)
    role_data = config.get('roles', {}).get(role_key)
    if role_data and isinstance(role_data, dict):
        return role_data.get('id', 0)
    return 0


def get_role_name(guild_id: int, role_key: str) -> str:
    """Get role name for a specific server and role type"""
    config = load_server_config(guild_id)
    role_data = config.get('roles', {}).get(role_key)
    if role_data and isinstance(role_data, dict):
        return role_data.get('name', 'Unknown')
    return 'Unknown'


def is_feature_enabled(guild_id: int, feature_key: str) -> bool:
    """Check if a feature is enabled for a specific server"""
    config = load_server_config(guild_id)
    return config.get('features', {}).get(feature_key, False)


def get_server_setting(guild_id: int, setting_key: str, default=None):
    """Get a server-specific setting"""
    config = load_server_config(guild_id)
    return config.get('settings', {}).get(setting_key, default)


def is_server_configured(guild_id: int) -> bool:
    """Check if a server has been configured"""
    config = load_server_config(guild_id)
    return bool(config.get('guild_id'))


# =============================================================================
# LEGACY COMPATIBILITY FUNCTIONS
# =============================================================================
# These functions maintain compatibility with your existing code

def get_legacy_channel_id(guild_id: int, legacy_name: str) -> int:
    """Get channel ID using legacy naming convention"""
    mapping = {
        'LOG_CHANNEL_ID': 'log_channel',
        'WELCOME_CHANNEL_ID': 'welcome_channel',
        'GOODBYE_CHANNEL_ID': 'goodbye_channel',
        'MEMBER_CHAT_CHANNEL_ID': 'member_chat_channel',
        'MESSAGE_HISTORY_CHANNEL_ID': 'message_history_channel',
        'ACHIEVEMENT_CHANNEL_ID': 'achievement_channel',
        'LEADERBOARD_CHANNEL_ID': 'leaderboard_channel',
        'TICKET_CHANNEL_ID': 'ticket_channel',
        'CASINO_CHANNEL_ID': 'casino_channel',
        'LOBBY_VOICE_CHANNEL_ID': 'lobby_voice',
    }

    if legacy_name in mapping:
        return get_channel_id(guild_id, mapping[legacy_name])
    return 0


def get_legacy_role_id(guild_id: int, legacy_name: str) -> int:
    """Get role ID using legacy naming convention"""
    mapping = {
        'STAFF_ROLE_ID': 'staff_role',
        'ADMIN_ROLE_ID': 'admin_role',
        'MEMBER_ROLE': 'member_role',
        'UNVERIFIED_ROLE_ID': 'unverified_role',
    }

    if legacy_name in mapping:
        return get_role_id(guild_id, mapping[legacy_name])
    return 0


# =============================================================================
# BACKWARDS COMPATIBILITY VARIABLES
# =============================================================================
# For existing code that expects these to be available - returns 0 for legacy code

# Legacy channel IDs - you should update your cogs to use guild_id parameter instead
LOG_CHANNEL_ID = 0
WELCOME_CHANNEL_ID = 0
GOODBYE_CHANNEL_ID = 0
MEMBER_CHAT_CHANNEL_ID = 0
MESSAGE_HISTORY_CHANNEL_ID = 0
ACHIEVEMENT_CHANNEL_ID = 0
LEADERBOARD_CHANNEL_ID = 0
TICKET_CHANNEL_ID = 0
CASINO_CHANNEL_ID = 0
LOBBY_VOICE_CHANNEL_ID = 0
TEMP_VOICE_CATEGORY_ID = 0
TICKET_CATEGORY_ID = 0
HISTORY_CHANNEL_ID = 0
RULES_CHANNEL_ID = 0
ROLE_ASSIGN_CHANNEL_ID = 0
ANNOUNCEMENTS_CHANNEL_ID = 0
INTERVIEW_PUBLIC_CHANNEL_ID = 0
INTERVIEW_PRIVATE_CHANNEL_ID = 0
CLAN_LEADERBOARD_CHANNEL_ID = 0

# Legacy role IDs
STAFF_ROLE_ID = 0
ADMIN_ROLE_ID = 0
MEMBER_ROLE = 0
UNVERIFIED_ROLE_ID = 0
ACCEPTED_ROLE_ID = 0
APPLICANT_ROLE_ID = 0
GUEST_ROLE_ID = 0

# These remain global as they don't vary per server
GUILD_ID = parse_int("GUILD_ID")  # Keep for legacy, but servers should use ctx.guild.id
AUTO_ROLE_IDS = parse_ids("AUTO_ROLE_IDS")

# Global bot constants
GHOST_HUNTER_ID = 1365499246962540606
PING_MASTER_ID = 1415130255521218722

# Google Sheets config
MEMBERS_SHEET_NAME = os.getenv("MEMBERS_SHEET_NAME", "Member List")
TEST_SHEET_NAME = os.getenv("TEST_SHEET_NAME", "Testing")
GSHEET_TESTING_SPREADSHEET_NAME = os.getenv("GSHEET_TESTING_SPREADSHEET_NAME", "Testing")
GSHEET_MEMBER_LIST_SPREADSHEET_NAME = os.getenv("GSHEET_MEMBER_LIST_SPREADSHEET_NAME", "Member List")


# =============================================================================
# UPDATED REACTION ROLE SYSTEM
# =============================================================================

def get_reaction_roles(guild_id: int) -> Dict[int, Dict[str, int]]:
    """Get reaction role mapping for a specific server"""
    config = load_server_config(guild_id)
    reaction_roles = config.get('reaction_roles', {})

    # Convert string keys to int for message IDs
    converted = {}
    for msg_id, emoji_map in reaction_roles.items():
        try:
            converted[int(msg_id)] = {
                emoji: int(role_id) for emoji, role_id in emoji_map.items()
            }
        except (ValueError, AttributeError):
            continue

    return converted


def set_reaction_roles(guild_id: int, message_id: int, emoji_role_map: Dict[str, int]) -> bool:
    """Set reaction roles for a specific message in a server"""
    try:
        config = load_server_config(guild_id)

        if 'reaction_roles' not in config:
            config['reaction_roles'] = {}

        config['reaction_roles'][str(message_id)] = {
            emoji: int(role_id) for emoji, role_id in emoji_role_map.items()
        }

        return save_server_config(guild_id, config)
    except Exception as e:
        print(f"Error setting reaction roles for guild {guild_id}: {e}")
        return False


# Legacy global reaction role map (empty - use per-server function)
REACTION_ROLE_MAP = {}


# =============================================================================
# UTILITY FUNCTIONS FOR MIGRATION
# =============================================================================

def migrate_legacy_env_to_server_config(guild_id: int, env_file_path: str = '.env') -> bool:
    """Migrate legacy .env configuration to new server-specific format"""
    try:
        # Load legacy .env
        legacy_env = {}
        if os.path.exists(env_file_path):
            with open(env_file_path, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith('#') and '=' in line:
                        key, value = line.split('=', 1)
                        legacy_env[key] = value.strip('"')

        # Create server config from legacy env
        server_config = {
            'guild_id': str(guild_id),
            'guild_name': 'Migrated Server',
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
            },
            'settings': {
                'starting_coins': 200
            }
        }

        # Map legacy channels
        channel_mapping = {
            'LOG_CHANNEL_ID': 'log_channel',
            'WELCOME_CHANNEL_ID': 'welcome_channel',
            'GOODBYE_CHANNEL_ID': 'goodbye_channel',
            'MEMBER_CHAT_CHANNEL_ID': 'member_chat_channel',
            'MESSAGE_HISTORY_CHANNEL_ID': 'message_history_channel',
            'ACHIEVEMENT_CHANNEL_ID': 'achievement_channel',
            'LEADERBOARD_CHANNEL_ID': 'leaderboard_channel',
            'TICKET_CHANNEL_ID': 'ticket_channel',
            'LOBBY_VOICE_CHANNEL_ID': 'lobby_voice',
            'CASINO_CHANNEL_ID': 'casino_channel',
        }

        for legacy_key, new_key in channel_mapping.items():
            if legacy_key in legacy_env and legacy_env[legacy_key] != '0':
                try:
                    channel_id = int(legacy_env[legacy_key])
                    server_config['channels'][new_key] = {
                        'id': channel_id,
                        'name': 'Migrated Channel'
                    }
                except ValueError:
                    pass

        # Map legacy roles
        role_mapping = {
            'STAFF_ROLE_ID': 'staff_role',
            'ADMIN_ROLE_ID': 'admin_role',
            'MEMBER_ROLE': 'member_role',
            'UNVERIFIED_ROLE_ID': 'unverified_role',
        }

        for legacy_key, new_key in role_mapping.items():
            if legacy_key in legacy_env and legacy_env[legacy_key] != '0':
                try:
                    role_id = int(legacy_env[legacy_key])
                    server_config['roles'][new_key] = {
                        'id': role_id,
                        'name': 'Migrated Role'
                    }
                except ValueError:
                    pass

        # Handle reaction roles
        if 'REACTION_ROLE_MAP_JSON' in legacy_env:
            try:
                import json
                reaction_map = json.loads(legacy_env['REACTION_ROLE_MAP_JSON'])
                server_config['reaction_roles'] = reaction_map
            except json.JSONDecodeError:
                pass

        # Save server config
        return save_server_config(guild_id, server_config)

    except Exception as e:
        print(f"Error migrating legacy config for guild {guild_id}: {e}")
        return False
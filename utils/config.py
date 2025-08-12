import os
import json
from dotenv import load_dotenv

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

load_dotenv(os.path.join(BASE_DIR, '.env'))

def parse_int(env_var_name, default=None):
    val = os.getenv(env_var_name)
    try:
        return int(val) if val is not None else default
    except ValueError:
        return default

def parse_ids(env_var):
    raw = os.getenv(env_var, "")
    return [int(x) for x in raw.split(",") if x.strip().isdigit()]

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
COMMAND_PREFIX = os.getenv("COMMAND_PREFIX", "!")

DATABASE_URL = os.getenv("DATABASE_URL")

LOG_CHANNEL_ID = parse_int("LOG_CHANNEL_ID")

LOBBY_VOICE_CHANNEL_ID = parse_int("LOBBY_VOICE_CHANNEL_ID")
TEMP_VOICE_CATEGORY_ID = parse_int("TEMP_VOICE_CATEGORY_ID")

TICKET_CATEGORY_ID = parse_int("TICKET_CATEGORY_ID")
STAFF_ROLE_ID = parse_int("STAFF_ROLE_ID")
HISTORY_CHANNEL_ID = parse_int("HISTORY_CHANNEL_ID")
TICKET_CHANNEL_ID = parse_int("TICKET_CHANNEL_ID")

INTERVIEW_PUBLIC_CHANNEL_ID = parse_int("INTERVIEW_PUBLIC_CHANNEL_ID")
INTERVIEW_PRIVATE_CHANNEL_ID = parse_int("INTERVIEW_PRIVATE_CHANNEL_ID")

WELCOME_CHANNEL_ID = int(os.getenv("WELCOME_CHANNEL_ID", "0"))
GOODBYE_CHANNEL_ID = int(os.getenv("GOODBYE_CHANNEL_ID", "0"))

RULES_CHANNEL_ID = int(os.getenv("RULES_CHANNEL_ID", "0"))
ROLE_ASSIGN_CHANNEL_ID = int(os.getenv("ROLE_ASSIGN_CHANNEL_ID", "0"))
ANNOUNCEMENTS_CHANNEL_ID = int(os.getenv("ANNOUNCEMENTS_CHANNEL_ID", "0"))

ACCEPTED_ROLE_ID = int(os.getenv("ACCEPTED_ROLE_ID", "0"))

MEMBER_CHAT_CHANNEL_ID = parse_int("MEMBER_CHAT_CHANNEL_ID")

CLAN_LEADERBOARD_CHANNEL_ID = parse_int("CLAN_LEADERBOARD_CHANNEL_ID")

GUILD_ID = int(os.getenv("GUILD_ID", "0"))

AUTO_ROLE_IDS = parse_ids("AUTO_ROLE_IDS")

APPLICANT_ROLE_ID = int(os.getenv("APPLICANT_ROLE_ID", 0))
GUEST_ROLE_ID = int(os.getenv("GUEST_ROLE_ID", 0))

# Load reaction_role_map from JSON in .env
reaction_role_json = os.getenv("REACTION_ROLE_MAP_JSON", "{}")

try:
    REACTION_ROLE_MAP = json.loads(reaction_role_json)
    # Optional: Convert message IDs to int for consistency
    REACTION_ROLE_MAP = {
        int(msg_id): {
            emoji: int(role_id) for emoji, role_id in emoji_map.items()
        } for msg_id, emoji_map in REACTION_ROLE_MAP.items()
    }
except json.JSONDecodeError as e:
    print(f"Failed to parse REACTION_ROLE_MAP_JSON: {e}")
    REACTION_ROLE_MAP = {}

GSHEET_CREDENTIALS_PATH = "exceed-465801-9a237edcd3b1.json"
MEMBERS_SHEET_NAME = os.getenv("MEMBERS_SHEET_NAME", "Member List")
TEST_SHEET_NAME = os.getenv("TEST_SHEET_NAME", "Testing")
GSHEET_TESTING_SPREADSHEET_NAME = os.getenv("GSHEET_TESTING_SPREADSHEET_NAME", "Testing")
GSHEET_MEMBER_LIST_SPREADSHEET_NAME = os.getenv("GSHEET_MEMBER_LIST_SPREADSHEET_NAME", "Member List")

MESSAGE_HISTORY_CHANNEL_ID = int(os.getenv("MESSAGE_HISTORY_CHANNEL_ID", "0"))

# Your bot's Discord user ID
BOT_ID = 1389668557759189052

# Achievement System Configuration
ACHIEVEMENT_DATA_PATH = "data/achievements.json"
GHOST_HUNTER_ID = 1365499246962540606
PING_MASTER_ID = 1389668557759189052
ACHIEVEMENT_CHANNEL_ID = 1404889663038029968
ACHIEVEMENT_ALERT_CHANNEL_ID = 1404898177458376734
HOLIDAYS = [
    "january 1",   # New Year's Day (Canada & USA)
    "february 14", # Valentine's Day (Canada & USA)
    "february 19", # Family Day (Canada - 3rd Monday in Feb, varies by province)
    "february 19", # Presidents' Day (USA - 3rd Monday in Feb)
    "march 17",    # St. Patrick's Day (Canada & USA)
    "april 1",     # April Fools' Day (Canada & USA)
    "july 1",      # Canada Day (Canada)
    "july 4",      # Independence Day (USA)
    "september 2", # Labour Day (Canada & USA - 1st Monday in Sept)
    "october 14",  # Thanksgiving (Canada - 2nd Monday in Oct)
    "october 31",  # Halloween (Canada & USA)
    "november 11", # Remembrance Day (Canada)
    "november 28", # Thanksgiving (USA - 4th Thursday in Nov)
    "december 25", # Christmas (Canada & USA)
    "december 26", # Boxing Day (Canada)
]

ACHIEVEMENT_EMOJIS = {
    "general": "🏆",
    "hidden": "🤫",
}
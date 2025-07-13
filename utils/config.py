import os
from pathlib import Path
from dotenv import load_dotenv
import re
from collections import defaultdict

load_dotenv()

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

# === Build REACTION_ROLE_MAP from env variables ===
reaction_role_map_raw = {k: v for k, v in os.environ.items() if k.startswith("REACTION_ROLE_")}

REACTION_ROLE_MAP = defaultdict(dict)
pattern = re.compile(r"REACTION_ROLE_(\d+)_(.+)")

for env_key, role_id_str in reaction_role_map_raw.items():
    match = pattern.match(env_key)
    if not match:
        continue

    message_id = match.group(1)      # e.g. '1391796467060178984'
    emoji_key = match.group(2)       # e.g. 'valo_radiant' or '🇼'

    try:
        role_id = int(role_id_str)
    except ValueError:
        continue

    REACTION_ROLE_MAP[message_id][emoji_key] = role_id

REACTION_ROLE_MAP = dict(REACTION_ROLE_MAP)  # convert defaultdict to dict

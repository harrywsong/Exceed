# /home/hws/Exceed/bot.py
import discord
import pytz
from discord.ext import commands, tasks
import os
import asyncio
import datetime
import aiohttp
import logging
import sys
import pathlib
import asyncpg
import inspect
from datetime import datetime, time as dt_time, timedelta
import re

# --- Flask API Imports ---
from flask import Flask, jsonify, request
from threading import Thread
import time
import subprocess
# --- End Flask API Imports ---

import utils.config as config
import utils.logger as logger_module
from cogs.achievements import PersistentAchievementView
from utils import upload_to_drive

from utils.database_updater import DatabaseUpdater

# --- Enhanced Bot Manager for Better Instance Management ---
class BotManager:
    """Singleton to manage bot instance and provide better API integration"""
    _instance = None

    def __init__(self):
        self.bot = None
        self._shutdown_event = asyncio.Event()

    @classmethod
    def get_instance(cls):
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def set_bot(self, bot):
        self.bot = bot

    def get_bot(self):
        return self.bot

    def signal_shutdown(self):
        if self._shutdown_event:
            self._shutdown_event.set()


# Global bot manager
bot_manager = BotManager.get_instance()

global bot_instance
bot_instance = None


# --- Database Functions (Enhanced with better error handling) ---
async def create_db_pool_in_bot():
    """Creates and returns a PostgreSQL connection pool with enhanced error handling."""
    try:
        database_url = config.DATABASE_URL
        if not database_url:
            raise ValueError("DATABASE_URL environment variable not set.")

        # Test connection first
        test_conn = await asyncpg.connect(database_url)
        await test_conn.close()

        pool = await asyncpg.create_pool(
            database_url,
            min_size=2,  # Reduced from 5 for better resource management
            max_size=8,  # Reduced from 10
            command_timeout=30,  # Reduced timeout
            server_settings={
                'application_name': 'exceed_discord_bot'
            }
        )
        return pool
    except Exception as e:
        print(f"Failed to create database pool using DATABASE_URL: {e}", file=sys.stderr)
        raise


async def add_reaction_role_to_db(pool, guild_id: int, message_id: int, channel_id: int, emoji: str, role_id: int):
    current_logger = logging.getLogger('discord')
    current_logger.debug(
        f"DB: Attempting to add reaction role for G:{guild_id}, M:{message_id}, C:{channel_id}, E:{emoji}, R:{role_id}")

    if not pool:
        current_logger.error("DB: No database pool available")
        return False

    async with pool.acquire() as conn:
        try:
            await conn.execute("""
                INSERT INTO reaction_roles_table (guild_id, message_id, channel_id, emoji, role_id)
                VALUES ($1, $2, $3, $4, $5)
                ON CONFLICT (message_id, emoji, role_id) DO NOTHING;
            """, guild_id, message_id, channel_id, emoji, role_id)
            current_logger.info(f"DB: Successfully inserted reaction role for message {message_id}, emoji {emoji}.")
            return True
        except Exception as db_e:
            current_logger.error(f"DB: Error inserting reaction role into DB: {db_e}", exc_info=True)
            return False


# --- Enhanced Flask API Setup ---
api_app = Flask(__name__)

# Suppress werkzeug logging more effectively
werkzeug_logger = logging.getLogger('werkzeug')
werkzeug_logger.setLevel(logging.ERROR)
for handler in list(werkzeug_logger.handlers):
    werkzeug_logger.removeHandler(handler)
werkzeug_logger.addHandler(logging.NullHandler())

# Enhanced regex patterns for log parsing
LOG_LINE_REGEX = re.compile(r"^\[(.*?)\] \[([A-Z]+)\s*\.*\] \[(.*?)\] (.*)$")
SIMPLE_LOG_REGEX = re.compile(r"^(DEBUG|INFO|WARNING|ERROR|CRITICAL|WARN):\s*(.*)$", re.IGNORECASE)

LEVEL_MAP = {
    'DEBUG': logging.DEBUG,
    'INFO': logging.INFO,
    'WARNING': logging.WARNING,
    'ERROR': logging.ERROR,
    'CRITICAL': logging.CRITICAL,
    'WARN': logging.WARNING
}


@api_app.route('/config', methods=['GET'])
def get_bot_config():
    """
    API endpoint to retrieve non-sensitive configuration data from utils.config.
    """
    if not bot_instance or not hasattr(bot_instance, 'logger') or bot_instance.logger is None:
        current_logger = logging.getLogger(__name__)
    else:
        current_logger = bot_instance.logger

    try:
        sensitive_keywords = ['TOKEN', 'SECRET', 'KEY', 'PASSWORD', 'DATABASE_URL', 'API', 'WEBHOOK']
        safe_config = {}

        # Get global config
        global_config = config.get_global_config()
        for key, value in global_config.items():
            if not any(keyword in key.upper() for keyword in sensitive_keywords):
                safe_config[key] = str(value) if value is not None else None

        # Add server count info
        if bot_instance and bot_instance.is_ready():
            all_server_configs = config.get_all_server_configs()
            safe_config['configured_servers'] = len(all_server_configs)
            safe_config['total_servers'] = len(bot_instance.guilds)

        current_logger.info("API: Successfully retrieved non-sensitive bot configuration.")
        return jsonify({"status": "success", "config": safe_config}), 200

    except Exception as e:
        current_logger.error(f"API Error: Failed to retrieve bot configuration: {e}", exc_info=True)
        return jsonify({"status": "error", "error": f"Failed to retrieve bot configuration: {e}"}), 500


@api_app.route('/logs', methods=['GET'])
def get_recent_logs():
    """
    Returns recent log entries from the bot's log file.
    """
    try:
        log_file_path = logger_module.LOG_FILE_PATH
        if not log_file_path.exists():
            if bot_instance and hasattr(bot_instance, 'logger'):
                bot_instance.logger.error(f"Log file not found at: {log_file_path}")
            else:
                logging.getLogger().error(f"Log file not found at: {log_file_path}")
            return jsonify({"status": "error", "message": "Log file not found."}), 404

        with open(log_file_path, 'r', encoding='utf-8', errors='ignore') as f:
            lines = f.readlines()
            recent_logs = []
            log_pattern = re.compile(
                r'^\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\] \[(.*?)(?:\.<(\d+))?\] \[(.*?)\] (.*)$')

            # Read latest 200 lines for efficiency
            for line in reversed(lines[-200:]):
                match = log_pattern.match(line.strip())
                if match:
                    timestamp, level_raw, thread_id_part, logger_name, message = match.groups()
                    level = level_raw.split('.')[0]
                    recent_logs.append({
                        "timestamp": timestamp,
                        "level": level.upper(),
                        "logger_name": logger_name,
                        "message": message
                    })
                else:
                    # Pattern doesn't match - try to extract level
                    level = "UNKNOWN"
                    if "DEBUG" in line.upper():
                        level = "DEBUG"
                    elif "INFO" in line.upper():
                        level = "INFO"
                    elif "WARNING" in line.upper():
                        level = "WARNING"
                    elif "ERROR" in line.upper():
                        level = "ERROR"
                    elif "CRITICAL" in line.upper():
                        level = "CRITICAL"

                    message_part = line.strip()
                    if len(message_part) > 200:
                        message_part = message_part[:200] + "..."

                    recent_logs.append({
                        "timestamp": datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                        "level": level,
                        "logger_name": "Unparsed",
                        "message": message_part
                    })

        return jsonify({"status": "success", "logs": recent_logs[::-1]}), 200
    except Exception as e:
        if bot_instance and hasattr(bot_instance, 'logger'):
            bot_instance.logger.error(f"Error retrieving logs: {e}", exc_info=True)
        else:
            logging.getLogger().error(f"Error retrieving logs: {e}", exc_info=True)
        return jsonify({"status": "error", "message": f"Failed to retrieve logs: {e}"}), 500


@api_app.route('/health')
def health_check():
    """Enhanced health check endpoint"""
    bot = bot_manager.get_bot()
    if not bot:
        return jsonify({"status": "error", "message": "Bot not initialized"}), 503

    health_status = {
        "bot_ready": bot.is_ready(),
        "database_connected": bool(bot.pool),
        "uptime": None,
        "latency_ms": None,
        "memory_usage_mb": None
    }

    if bot.is_ready():
        uptime = datetime.now(pytz.utc) - bot.start_time
        health_status["uptime"] = str(uptime)
        health_status["latency_ms"] = round(bot.latency * 1000, 2) if bot.latency else None

        # Memory usage
        import psutil
        process = psutil.Process()
        health_status["memory_usage_mb"] = round(process.memory_info().rss / 1024 / 1024, 2)

    overall_status = "healthy" if all([
        health_status["bot_ready"],
        health_status["database_connected"]
    ]) else "unhealthy"

    status_code = 200 if overall_status == "healthy" else 503
    return jsonify({"status": overall_status, "details": health_status}), status_code


@api_app.route('/status')
def bot_status():
    """Enhanced status endpoint with more detailed information"""
    bot = bot_manager.get_bot()
    if bot and bot.is_ready():
        uptime = datetime.now(pytz.utc) - bot.start_time
        hours, remainder = divmod(int(uptime.total_seconds()), 3600)
        minutes, seconds = divmod(remainder, 60)
        uptime_str = f"{hours}h {minutes}m {seconds}s"

        latency_ms = round(bot.latency * 1000, 2) if bot.latency else 'N/A'

        # Check if coins system is loaded
        coins_loaded = 'cogs.coins' in bot.extensions

        # Get configured server count
        all_server_configs = config.get_all_server_configs()
        configured_servers = len(all_server_configs)

        return jsonify({
            "status": "Online",
            "uptime": uptime_str,
            "latency_ms": latency_ms,
            "guild_count": len(bot.guilds),
            "configured_servers": configured_servers,
            "user_count": len(bot.users),
            "commands_used_today": getattr(bot, 'total_commands_today', 0),
            "database_available": bool(bot.pool),
            "coins_system_loaded": coins_loaded,
            "cogs_loaded": list(bot.extensions.keys()),
            "message": "Bot is running and ready."
        })
    else:
        return jsonify({
            "status": "Offline",
            "uptime": "N/A",
            "latency_ms": "N/A",
            "guild_count": 0,
            "configured_servers": 0,
            "user_count": 0,
            "commands_today": 0,
            "database_available": False,
            "error": "Bot is not ready or offline."
        }), 503


@api_app.route('/api/guilds', methods=['GET'])
def get_guilds():
    try:
        bot_instance = bot_manager.get_bot()
        if not bot_instance or not bot_instance.is_ready():
            return jsonify({"status": "error", "error": "Bot is not ready."}), 503

        guild_list = []
        all_server_configs = config.get_all_server_configs()

        for guild in bot_instance.guilds:
            is_configured = str(guild.id) in all_server_configs
            guild_config = all_server_configs.get(str(guild.id), {})

            guild_list.append({
                "id": str(guild.id),
                "name": guild.name,
                "icon_url": str(guild.icon.url) if guild.icon else None,
                "member_count": guild.member_count,
                "channel_count": len(guild.channels),
                "owner_name": str(guild.owner),
                "owner_id": str(guild.owner_id),
                "configured": is_configured,
                "features_enabled": len(
                    [f for f in guild_config.get('features', {}).values() if f]) if is_configured else 0
            })
        return jsonify(guild_list), 200
    except Exception as e:
        return jsonify({"status": "error", "error": str(e)}), 500


@api_app.route('/command_stats')
def command_stats():
    """Enhanced command statistics with error handling"""
    bot = bot_manager.get_bot()
    if bot:
        stats_list = []
        command_counts = getattr(bot, 'command_counts', {})
        for cmd, count in command_counts.items():
            stats_list.append({"command_name": cmd, "usage_count": count})
        stats_list.sort(key=lambda x: x['usage_count'], reverse=True)
        return jsonify({
            "status": "success",
            "command_stats": stats_list,
            "total_commands_today": getattr(bot, 'total_commands_today', 0)
        })
    else:
        return jsonify({
            "status": "error",
            "error": "Bot instance not available."
        }), 503


@api_app.route('/api/logs')
def get_logs():
    """Enhanced logs endpoint with better filtering and error handling"""
    log_file_path = logger_module.LOG_FILE_PATH
    requested_level_str = request.args.get('level', '').upper()
    requested_level_int = LEVEL_MAP.get(requested_level_str, None)
    limit = min(int(request.args.get('limit', '500')), 2000)  # Max 2000 lines

    since_timestamp_str = request.args.get('since_timestamp')
    comparison_timestamp = None
    if since_timestamp_str:
        try:
            comparison_timestamp = datetime.strptime(since_timestamp_str, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            return jsonify({
                "status": "error",
                "message": "Invalid since_timestamp format. Use YYYY-MM-DD HH:MM:SS."
            }), 400

    try:
        if not os.path.exists(log_file_path):
            return jsonify({"status": "error", "error": "Log file not found."}), 404

        with open(log_file_path, 'r', encoding='utf-8', errors='ignore') as f:
            # Read only the last portion of the file for efficiency
            f.seek(0, 2)  # Go to end
            file_size = f.tell()
            # Read last 1MB or entire file if smaller
            read_size = min(file_size, 1024 * 1024)
            f.seek(max(0, file_size - read_size))
            if file_size > read_size:
                f.readline()  # Skip partial line
            lines = f.readlines()

        parsed_and_filtered_logs = []
        for line in reversed(lines[-limit:]):  # Process in reverse for recent-first
            stripped_line = line.strip()

            # Skip API noise
            if any(noise in stripped_line for noise in [
                "GET /status HTTP/1.1",
                "GET /logs HTTP/1.1",
                "GET /command_stats HTTP/1.1",
                "GET /health HTTP/1.1",
                "INFO....] [werkzeug]"
            ]):
                continue

            parsed_entry = None
            log_line_timestamp = None

            match_structured = LOG_LINE_REGEX.match(stripped_line)
            if match_structured:
                timestamp_str, level_raw, logger_name, message = match_structured.groups()
                log_level_int = LEVEL_MAP.get(level_raw, logging.INFO)

                try:
                    log_line_timestamp = datetime.strptime(timestamp_str, "%Y-%m-%d %H:%M:%S")
                except ValueError:
                    timestamp_str = "N/A"

                parsed_entry = {
                    "timestamp": timestamp_str,
                    "level": level_raw,
                    "logger_name": logger_name,
                    "message": message.strip()
                }
            else:
                match_simple = SIMPLE_LOG_REGEX.match(stripped_line)
                if match_simple:
                    level_raw, message = match_simple.groups()
                    log_level_int = LEVEL_MAP.get(level_raw.upper(), logging.INFO)
                    parsed_entry = {
                        "timestamp": "N/A",
                        "level": level_raw.upper(),
                        "logger_name": "ROOT",
                        "message": message.strip()
                    }
                else:
                    log_level_int = logging.INFO
                    parsed_entry = {
                        "timestamp": "N/A",
                        "level": "RAW",
                        "logger_name": "N/A",
                        "message": stripped_line
                    }

            # Apply timestamp filter
            if comparison_timestamp and log_line_timestamp:
                if log_line_timestamp <= comparison_timestamp:
                    continue

            # Apply level filter
            if parsed_entry and (requested_level_int is None or log_level_int >= requested_level_int):
                parsed_and_filtered_logs.append(parsed_entry)

        # Reverse to get chronological order (oldest first)
        parsed_and_filtered_logs.reverse()

        return jsonify({
            "status": "success",
            "logs": parsed_and_filtered_logs,
            "total_returned": len(parsed_and_filtered_logs)
        })
    except Exception as e:
        print(f"Error reading log file: {e}", file=sys.stderr)
        return jsonify({"status": "error", "error": f"Failed to read log file: {e}"}), 500


@api_app.route('/control/<action>', methods=['POST'])
def control_bot_api(action):
    """Enhanced bot control with better error handling"""
    bot = bot_manager.get_bot()
    if not bot:
        return jsonify({"status": "error", "error": "Bot instance not available."}), 500

    try:
        if action == 'restart':
            bot.logger.info("API request: Bot restart initiated...")
            asyncio.run_coroutine_threadsafe(bot.graceful_shutdown(), bot.loop)
            return jsonify({"status": "success", "message": "Bot restart initiated."})

        elif action == 'reload_cogs':
            bot.logger.info("API request: Reloading all cogs...")
            future = asyncio.run_coroutine_threadsafe(bot.reload_all_cogs(), bot.loop)
            try:
                future.result(timeout=30)  # 30 second timeout
                return jsonify({"status": "success", "message": "All cogs reloaded successfully."})
            except asyncio.TimeoutError:
                return jsonify({"status": "error", "error": "Cog reload timed out"}), 504
            except Exception as e:
                bot.logger.error(f"Cog reload failed: {e}")
                return jsonify({"status": "error", "error": f"Failed to reload cogs: {e}"}), 500

        elif action == 'update_git':
            bot.logger.info("API request: Git update...")
            try:
                result = subprocess.run(['git', 'pull'], capture_output=True, text=True,
                                        cwd=os.getcwd(), timeout=60)
                if result.returncode == 0:
                    bot.logger.info(f"Git pull successful: {result.stdout.strip()}")
                    return jsonify({"status": "success", "message": "Git pull successful."})
                else:
                    bot.logger.error(f"Git pull failed: {result.stderr.strip()}")
                    return jsonify({"status": "error", "error": f"Git pull failed: {result.stderr.strip()}"}), 500
            except subprocess.TimeoutExpired:
                return jsonify({"status": "error", "error": "Git pull timed out"}), 504
            except Exception as e:
                bot.logger.error(f"Error during git update: {e}")
                return jsonify({"status": "error", "error": f"Error during git update: {e}"}), 500
        else:
            return jsonify(
                {"status": "error", "error": "Invalid control action. Valid: restart, reload_cogs, update_git"}), 400

    except Exception as e:
        if bot and hasattr(bot, 'logger'):
            bot.logger.error(f"Control API error for action '{action}': {e}", exc_info=True)
        return jsonify({"status": "error", "error": f"Unexpected error: {e}"}), 500


@api_app.route('/command/announce', methods=['POST'])
def send_announcement_api():
    """Enhanced announcement endpoint with validation"""
    bot = bot_manager.get_bot()
    if not bot or not bot.is_ready():
        return jsonify({"status": "error", "error": "Bot is not ready."}), 503

    try:
        data = request.get_json()
        if not data:
            return jsonify({"status": "error", "error": "No JSON data provided"}), 400

        channel_id = data.get('channel_id')
        message = data.get('message')

        if not channel_id or not message:
            return jsonify({"status": "error", "error": "Both channel_id and message are required."}), 400

        if len(message) > 2000:
            return jsonify({"status": "error", "error": "Message too long (max 2000 characters)"}), 400

        try:
            channel = bot.get_channel(int(channel_id))
            if not channel:
                return jsonify({"status": "error", "error": "Channel not found or bot lacks access."}), 404

            future = asyncio.run_coroutine_threadsafe(channel.send(message), bot.loop)
            future.result(timeout=10)  # 10 second timeout

            bot.logger.info(f"API request: Announcement sent to channel {channel_id}.")
            return jsonify({"status": "success", "message": "Announcement sent successfully."})

        except ValueError:
            return jsonify({"status": "error", "error": "Invalid channel ID format."}), 400
        except asyncio.TimeoutError:
            return jsonify({"status": "error", "error": "Message send timed out"}), 504
        except discord.Forbidden:
            return jsonify({"status": "error", "error": "Bot lacks permission to send messages"}), 403

    except Exception as e:
        if bot and hasattr(bot, 'logger'):
            bot.logger.error(f"Announcement send failed: {e}", exc_info=True)
        return jsonify({"status": "error", "error": f"Unexpected error: {e}"}), 500


# Additional API endpoints (keeping existing ones but with enhancements)
@api_app.route('/api/reaction_roles', methods=['GET'])
def get_reaction_roles_api():
    bot = bot_manager.get_bot()
    if not bot or not bot.pool:
        return jsonify({"error": "Bot or database not available."}), 503

    try:
        future = asyncio.run_coroutine_threadsafe(
            fetch_reaction_roles_from_db(bot.pool), bot.loop
        )
        reaction_roles_data = future.result(timeout=10)
        return jsonify(reaction_roles_data), 200
    except asyncio.TimeoutError:
        return jsonify({"error": "Database query timed out"}), 504
    except Exception as e:
        if bot and hasattr(bot, 'logger'):
            bot.logger.error(f"Error in /api/reaction_roles: {e}", exc_info=True)
        return jsonify({"error": "Failed to fetch reaction roles."}), 500


@api_app.route('/api/reaction_roles/add', methods=['POST'])
def add_reaction_role_api():
    bot = bot_manager.get_bot()
    if not bot:
        return jsonify({"error": "Bot not available"}), 503

    current_logger = bot.logger if hasattr(bot, 'logger') else logging.getLogger()

    try:
        data = request.get_json()
        if not data:
            return jsonify({"error": "No JSON data provided"}), 400

        required_fields = ['guild_id', 'message_id', 'channel_id', 'emoji', 'role_id']
        missing_fields = [field for field in required_fields if field not in data]
        if missing_fields:
            return jsonify({"error": f"Missing required fields: {', '.join(missing_fields)}"}), 400

        try:
            guild_id = int(data['guild_id'])
            message_id = int(data['message_id'])
            channel_id = int(data['channel_id'])
            role_id = int(data['role_id'])
            emoji = str(data['emoji'])
        except (ValueError, TypeError) as e:
            return jsonify({"error": f"Invalid data type: {e}"}), 400

        if not bot.pool:
            return jsonify({"error": "Database not available."}), 503

        future = asyncio.run_coroutine_threadsafe(
            add_reaction_role_to_db(bot.pool, guild_id, message_id, channel_id, emoji, role_id),
            bot.loop
        )
        result = future.result(timeout=10)

        if result:
            return jsonify({"success": True, "message": "Reaction role added successfully"}), 201
        else:
            return jsonify({"success": False, "message": "Failed to add reaction role"}), 500

    except asyncio.TimeoutError:
        return jsonify({"error": "Database operation timed out"}), 504
    except Exception as e:
        current_logger.error(f"API error in reaction_roles/add: {e}", exc_info=True)
        return jsonify({"error": "Internal server error"}), 500


async def fetch_reaction_roles_from_db(pool):
    """Fetches reaction roles from the database."""
    async with pool.acquire() as conn:
        records = await conn.fetch("""
            SELECT message_id, channel_id, emoji, role_id
            FROM reaction_roles_table
        """)
        return [dict(r) for r in records]


def run_api_server():
    """Enhanced API server runner with better error handling"""
    try:
        # Check if port is available
        import socket
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        result = sock.connect_ex(('127.0.0.1', 5001))
        sock.close()

        if result == 0:
            print("Port 5001 already in use. API server not started.")
            return

        api_app.run(
            host='127.0.0.1',
            port=5001,
            debug=False,
            use_reloader=False,
            threaded=True
        )
    except Exception as e:
        logging.getLogger().error(f"API server error: {e}", exc_info=True)


# --- Enhanced Bot Class ---
class MyBot(commands.Bot):
    def __init__(self, command_prefix, intents):
        super().__init__(command_prefix=command_prefix, intents=intents)
        self.start_time = datetime.now(pytz.utc)
        self.pool = None
        self.session = None
        self.command_counts = {}
        self.total_commands_today = 0
        self.logger = logging.getLogger('discord')
        self._shutdown_requested = False
        self._cleanup_tasks = []

    async def setup_hook(self):
        """Enhanced setup with better error handling and graceful degradation"""
        # Initialize HTTP session first
        self.session = aiohttp.ClientSession()

        # Initialize database pool with graceful degradation
        try:
            self.pool = await create_db_pool_in_bot()
            self.logger.info("✅ Database connection pool created successfully.")

            # Run database schema updates
            try:
                db_updater = DatabaseUpdater(self.pool)
                await db_updater.update_database_schema()

                # Clean legacy data for the first configured guild
                all_configs = config.get_all_server_configs()
                if all_configs:
                    first_guild_id = int(list(all_configs.keys())[0])
                    await db_updater.clean_legacy_data(first_guild_id)

                self.logger.info("✅ Database schema updated successfully.")
            except Exception as e:
                self.logger.error(f"⚠️ Database schema update failed: {e}", exc_info=True)
                self.logger.warning("Continuing with existing schema...")

        except Exception as e:
            self.logger.error(f"❌ Database pool creation failed: {e}", exc_info=True)
            self.logger.warning("⚠️ Running in limited mode without database.")
            self.pool = None

        # Handle existing log files
        await self._handle_startup_logs()

        # Get a valid log channel from any configured server for global logging
        log_channel_id = self._get_global_log_channel()

        # Configure enhanced logging
        try:
            logger_module._configure_root_handlers(
                bot=self,
                discord_log_channel_id=log_channel_id
            )
            self.logger = logging.getLogger('main_bot')
            self.logger.info("✅ Bot logger configured successfully.")
        except Exception as e:
            self.logger.error(f"❌ Error setting up logger: {e}", exc_info=True)

        # Load extensions with dependency management
        await self._load_extensions_with_dependencies()

        # Sync slash commands with timeout
        try:
            async with asyncio.timeout(30):
                synced = await self.tree.sync()
                self.logger.info(f"✅ Synced {len(synced)} slash commands.")
        except asyncio.TimeoutError:
            self.logger.error("❌ Slash command sync timed out")
        except Exception as e:
            self.logger.error(f"❌ Slash command sync failed: {e}", exc_info=True)

        # Add persistent views - COMMENT OUT THE PROBLEMATIC LINE
        try:
            # self.add_view(PersistentAchievementView(self))  # Comment this out for now

            # Add coins persistent views if available
            try:
                from cogs.coins import CoinsView, LeaderboardView
                self.add_view(CoinsView(self))
                # self.add_view(LeaderboardView(self))
            except ImportError:
                self.logger.warning("⚠️ Coins views not available")

            self.logger.info("✅ Persistent views registered successfully.")
        except Exception as e:
            self.logger.error(f"❌ Persistent view registration failed: {e}", exc_info=True)

        import pathlib
        data_dir = pathlib.Path("data")
        data_dir.mkdir(exist_ok=True)
        self.logger.info("✅ Data directory prepared")
    def _get_global_log_channel(self) -> int:
        """Get a log channel from any configured server for global bot logging"""
        try:
            all_configs = config.get_all_server_configs()
            for guild_config in all_configs.values():
                log_channel = guild_config.get('channels', {}).get('log_channel')
                if log_channel and isinstance(log_channel, dict):
                    return log_channel.get('id', 0)
        except Exception as e:
            self.logger.warning(f"Could not find global log channel: {e}")
        return 0

    async def _handle_startup_logs(self):
        """Handle existing log files on startup"""
        if os.path.exists(logger_module.LOG_FILE_PATH) and os.path.getsize(logger_module.LOG_FILE_PATH) > 0:
            self.logger.info("⚠️ Previous log file detected. Uploading to Google Drive...")
            try:
                timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
                startup_log_filename = f"startup_log_{timestamp}.log"
                startup_log_path = logger_module.LOG_FILE_PATH.parent / startup_log_filename
                os.rename(logger_module.LOG_FILE_PATH, startup_log_path)

                # Upload in executor to avoid blocking
                await self.loop.run_in_executor(
                    None, upload_to_drive.upload_log_to_drive, str(startup_log_path)
                )
                self.logger.info(f"✅ startup_log_{timestamp}.log file uploaded successfully.")
            except Exception as e:
                self.logger.error(f"❌ Failed to handle startup log file: {e}", exc_info=True)

    async def _load_extensions_with_dependencies(self):
        """Load extensions with proper dependency order"""
        # Core extensions (no dependencies) - COINS MUST BE FIRST
        core_extensions = [
            'cogs.coins',  # Move this to the very beginning
            'cogs.clear_messages',
            'cogs.voice',
            'cogs.welcomegoodbye',
            'cogs.message_history',
            'cogs.recording',
            'cogs.admin',
            'cogs.setup',
        ]

        # Casino extensions (depend on coins)
        casino_extensions = [
            'cogs.casino_base',
            'cogs.casino_blackjack',
            'cogs.casino_roulette',
            'cogs.casino_dice',
            'cogs.casino_coinflip',
            'cogs.casino_hilow',
            'cogs.casino_lottery',
            'cogs.casino_crash',
            'cogs.casino_minesweeper',
            'cogs.casino_slots',
            'cogs.casino_bingo',
        ]

        # Database-dependent extensions
        db_extensions = [
            'cogs.achievements',
            'cogs.registration',
            'cogs.reaction_roles',
            'cogs.ticket',
        ] if self.pool else []

        # API-dependent extensions
        api_extensions = [
            'cogs.autoguest',
        ]

        extension_groups = [
            ("Core", core_extensions),
            ("Casino", casino_extensions),
            ("Database", db_extensions),
            ("API", api_extensions)
        ]

        for group_name, extensions in extension_groups:
            if not extensions:
                self.logger.info(f"⭕ Skipping {group_name} extensions (no dependencies)")
                continue

            self.logger.info(f"📦 Loading {group_name} extensions...")
            for ext in extensions:
                try:
                    await self.load_extension(ext)
                    self.logger.info(f"✅ Cog loaded: {ext}")
                except commands.ExtensionAlreadyLoaded:
                    self.logger.warning(f"⚠️ Cog '{ext}' is already loaded.")
                except commands.ExtensionFailed as e:
                    self.logger.error(f"❌ Cog '{ext}' load failed (setup error): {e}", exc_info=True)
                except commands.ExtensionNotFound:
                    self.logger.error(f"❌ Cog '{ext}' not found.")
                except Exception as e:
                    self.logger.error(f"❌ Unexpected error loading cog '{ext}': {e}", exc_info=True)

    async def reload_all_cogs(self):
        """Enhanced cog reloading with better error handling"""
        reloaded_count = 0
        failed_count = 0

        for ext in list(self.extensions.keys()):
            try:
                await self.reload_extension(ext)
                self.logger.info(f"🔄 Cog reloaded: {ext}")
                reloaded_count += 1
            except commands.ExtensionNotLoaded:
                self.logger.warning(f"⚠️ Cog '{ext}' was not loaded, cannot reload.")
                failed_count += 1
            except commands.ExtensionFailed as e:
                self.logger.error(f"❌ Cog '{ext}' reload failed: {e}", exc_info=True)
                failed_count += 1
            except Exception as e:
                self.logger.error(f"❌ Unexpected error reloading cog '{ext}': {e}", exc_info=True)
                failed_count += 1

        self.logger.info(f"📊 Cog reload complete: {reloaded_count} successful, {failed_count} failed")

    @tasks.loop(minutes=10)
    async def update_presence(self):
        """Enhanced presence update with error handling"""
        try:
            guild_count = len(self.guilds)
            configured_count = len(config.get_all_server_configs())
            await self.change_presence(
                status=discord.Status.online,
                activity=discord.Activity(
                    type=discord.ActivityType.playing,
                    name=f"Managing {guild_count} servers | {configured_count} configured"
                )
            )
        except Exception as e:
            self.logger.error(f"❌ Presence update failed: {e}", exc_info=True)

    async def on_ready(self):
        """Enhanced on_ready with better initialization"""
        self.logger.info(f"--- Bot login complete: {self.user} (ID: {self.user.id}) ---")
        self.logger.info(f"Bot connected to the following guilds:")

        all_configs = config.get_all_server_configs()
        configured_count = 0

        for guild in self.guilds:
            is_configured = str(guild.id) in all_configs
            status = "✅" if is_configured else "❌"
            if is_configured:
                configured_count += 1
            self.logger.info(f"{status} {guild.name} (ID: {guild.id}) - {guild.member_count} members")

        self.logger.info(f"Configured servers: {configured_count}/{len(self.guilds)}")
        self.logger.info(f"Current latency: {round(self.latency * 1000)}ms")
        self.logger.info(f"Database connection: {'✅' if self.pool else '❌'}")

        # Set initial presence
        try:
            await self.change_presence(activity=discord.Game(name="Multi-server management!"))
        except Exception as e:
            self.logger.error(f"❌ Initial presence setting failed: {e}")

        # Start presence update loop
        if not self.update_presence.is_running():
            self.update_presence.start()

        # Handle crash logs in executor
        try:
            await self.loop.run_in_executor(None, check_crash_log_and_handle, self.logger)
        except Exception as e:
            self.logger.error(f"❌ Crash log handling failed: {e}", exc_info=True)

        # Start daily log uploader
        if not self.daily_log_uploader.is_running():
            self.daily_log_uploader.start()

        self.logger.info("🚀 Bot is fully ready!")

    async def on_guild_join(self, guild):
        """Handle new guild joins"""
        self.logger.info(f"🆕 Joined new guild: {guild.name} (ID: {guild.id}) - {guild.member_count} members")

        # Check if we have permissions to create channels
        if guild.me.guild_permissions.manage_channels:
            try:
                # Create a welcome embed
                embed = discord.Embed(
                    title="🎉 Thanks for inviting Analog!",
                    description="Hi there! I'm Analog, a multi-feature Discord bot ready to enhance your server.\n\n"
                                "🔧 **Get Started:**\n"
                                "Run `/bot-setup` to configure me for your server\n\n"
                                "✨ **Features Available:**\n"
                                "• Casino games and economy system\n"
                                "• Achievement system\n"
                                "• Ticket support system\n"
                                "• Voice channel management\n"
                                "• Welcome/goodbye messages\n"
                                "• And much more!\n\n"
                                "Need help? Run `/bot-status` to see configuration status.",
                    color=0x00ff00
                )

                # Try to send to system channel, otherwise general channel
                channel = guild.system_channel
                if not channel:
                    channel = discord.utils.get(guild.text_channels, name="general")
                if not channel:
                    channel = guild.text_channels[0] if guild.text_channels else None

                if channel and channel.permissions_for(guild.me).send_messages:
                    await channel.send(embed=embed)
                    self.logger.info(f"📨 Sent welcome message to {guild.name}")
            except Exception as e:
                self.logger.error(f"❌ Failed to send welcome message to {guild.name}: {e}")

    async def on_guild_remove(self, guild):
        """Handle guild removal"""
        self.logger.info(f"👋 Removed from guild: {guild.name} (ID: {guild.id})")

    async def on_command_completion(self, context):
        """Enhanced command completion tracking"""
        try:
            command_name = context.command.name if context.command else "unknown"
            self.command_counts[command_name] = self.command_counts.get(command_name, 0) + 1
            self.total_commands_today += 1

            user_info = f"{context.author} (ID: {context.author.id})" if context.author else "Unknown User"
            self.logger.info(f"User {user_info} used command '{command_name}'.")
        except Exception as e:
            self.logger.error(f"❌ Command completion tracking failed: {e}", exc_info=True)

    @commands.Cog.listener()
    async def on_app_command_completion(self, interaction: discord.Interaction, command: discord.app_commands.Command):
        """Enhanced slash command completion tracking"""
        try:
            command_name = command.name if command else "unknown"
            user_name = interaction.user.display_name if interaction.user else "Unknown User"
            user_id = interaction.user.id if interaction.user else "Unknown ID"

            self.command_counts[command_name] = self.command_counts.get(command_name, 0) + 1
            self.total_commands_today += 1
            self.logger.info(f"User {user_name} ({user_id}) used slash command '/{command_name}'.")
        except Exception as e:
            self.logger.error(f"❌ Slash command completion tracking failed: {e}", exc_info=True)

    async def on_command_error(self, context, error):
        """Enhanced global command error handler with detailed logging"""
        error_id = f"ERR-{datetime.now().strftime('%Y%m%d-%H%M%S')}-{id(error) % 10000}"

        # Don't log CommandNotFound as it's noise
        if isinstance(error, commands.CommandNotFound):
            return

        # Log error with context
        error_context = {
            'error_id': error_id,
            'user_id': context.author.id if context.author else None,
            'guild_id': context.guild.id if context.guild else None,
            'channel_id': context.channel.id if context.channel else None,
            'command': str(context.command) if context.command else None,
            'message_content': context.message.content[:200] if context.message else None
        }

        self.logger.error(
            f"Command error [{error_id}] in '{context.command}' by {context.author}: {error}",
            exc_info=True,
            extra=error_context
        )

        # User-friendly error responses
        error_messages = {
            commands.MissingPermissions: "❌ You don't have permission to use this command.",
            commands.MissingRequiredArgument: f"❌ Required argument missing. (Error ID: {error_id})",
            commands.BadArgument: f"❌ Invalid argument. (Error ID: {error_id})",
            commands.NoPrivateMessage: "❌ This command cannot be used in DMs.",
            commands.CommandOnCooldown: f"❌ Command on cooldown. Try again in {error.retry_after:.1f} seconds.",
            commands.BotMissingPermissions: f"❌ Bot missing required permissions: {', '.join(error.missing_permissions)}",
        }

        message = error_messages.get(type(error),
                                     f"❌ An unexpected error occurred. (Error ID: {error_id})")

        try:
            # Try to send error message
            if hasattr(context, 'send'):
                await context.send(message, ephemeral=True)
            elif hasattr(context, 'response') and not context.response.is_done():
                await context.response.send_message(message, ephemeral=True)
        except Exception as send_error:
            self.logger.error(f"Failed to send error message for {error_id}: {send_error}")

    @tasks.loop(time=dt_time(hour=4, minute=0))
    async def daily_log_uploader(self):
        """Enhanced daily log uploader with better error handling"""
        try:
            log_dir = logger_module.LOG_FILE_PATH.parent
            self.logger.info("Starting daily log upload task.")

            yesterday_date = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
            expected_rotated_log_name = f"log.log.{yesterday_date}"
            rotated_log_path = log_dir / expected_rotated_log_name

            if rotated_log_path.exists() and rotated_log_path.stat().st_size > 0:
                self.logger.info(
                    f"Found yesterday's rotated log file: '{expected_rotated_log_name}'. Uploading to Google Drive...")
                try:
                    # Upload in executor to avoid blocking
                    await self.loop.run_in_executor(
                        None, upload_to_drive.upload_log_to_drive, str(rotated_log_path)
                    )
                    self.logger.info(f"'{expected_rotated_log_name}' file uploaded and deleted successfully.")
                except Exception as e:
                    self.logger.error(f"Failed to upload '{expected_rotated_log_name}' file: {e}", exc_info=True)
            else:
                self.logger.info(f"No rotated log file for yesterday ({yesterday_date}) or file is empty.")

        except Exception as e:
            self.logger.error(f"Daily log upload task failed: {e}", exc_info=True)

    @daily_log_uploader.before_loop
    async def before_daily_log_uploader(self):
        """Wait for bot to be ready before starting daily log uploader"""
        await self.wait_until_ready()
        self.logger.info("Waiting for daily log uploader to be ready...")

    async def graceful_shutdown(self):
        """Enhanced graceful shutdown with comprehensive cleanup"""
        if self._shutdown_requested:
            self.logger.info("Already shutting down...")
            return

        self._shutdown_requested = True
        self.logger.info("Bot shutdown initiated...")

        try:
            # Cancel all background tasks including coins tasks
            tasks_to_cancel = [
                ('update_presence', self.update_presence),
                ('daily_log_uploader', self.daily_log_uploader)
            ]

            # Add coins tasks if they exist
            coins_cog = self.get_cog('CoinsCog')
            if coins_cog and hasattr(coins_cog, 'maintenance_leaderboard_update'):
                tasks_to_cancel.append(('coins_maintenance', coins_cog.maintenance_leaderboard_update))

            for task_name, task in tasks_to_cancel:
                if hasattr(task, 'is_running') and task.is_running():
                    self.logger.info(f"Stopping {task_name} task...")
                    task.cancel()
                    try:
                        await asyncio.wait_for(task._task, timeout=5.0)
                    except (asyncio.TimeoutError, asyncio.CancelledError):
                        self.logger.warning(f"Task {task_name} force terminated")

            # Cancel custom cleanup tasks
            for task in self._cleanup_tasks:
                if not task.done():
                    task.cancel()

            # Close database pool
            if self.pool:
                self.logger.info("Closing database pool connection...")
                await self.pool.close()
                self.logger.info("Database pool connection closed successfully.")

            # Close HTTP session
            if self.session and not self.session.closed:
                self.logger.info("Closing HTTP session...")
                await self.session.close()
                self.logger.info("HTTP session closed successfully.")

            # Close Discord connection
            self.logger.info("Closing Discord connection...")
            await self.close()

            self.logger.info("Bot shut down successfully.")

        except Exception as e:
            self.logger.error(f"Error during shutdown: {e}", exc_info=True)
            # Force close if graceful shutdown fails
            try:
                await self.close()
            except:
                pass


# --- Crash Log Handling (Enhanced) ---
CRASH_LOG_DIR = pathlib.Path(__file__).parent.parent / "logs"
CRASH_LOG_FILE = CRASH_LOG_DIR / "crash_log.txt"
CRASH_LOG_DIR.mkdir(parents=True, exist_ok=True)


def check_crash_log_and_handle(logger_instance: logging.Logger):
    """Enhanced crash log handling with better error recovery"""
    try:
        if CRASH_LOG_FILE.exists() and CRASH_LOG_FILE.stat().st_size > 0:
            logger_instance.warning("Previous bot crash log file detected. Uploading to Google Drive...")
            try:
                upload_to_drive.upload_log_to_drive(str(CRASH_LOG_FILE))
                logger_instance.info("Crash log file uploaded and deleted successfully.")
            except Exception as e:
                logger_instance.error(f"Failed to upload or delete crash log file: {e}", exc_info=True)
                # Try to rename the file so it doesn't interfere with future runs
                try:
                    backup_name = f"crash_log_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
                    CRASH_LOG_FILE.rename(CRASH_LOG_FILE.parent / backup_name)
                    logger_instance.info(f"Crash log backed up as {backup_name}.")
                except Exception as rename_error:
                    logger_instance.error(f"Failed to backup crash log: {rename_error}")
        else:
            logger_instance.info("No pending crash log file to process.")
    except Exception as e:
        logger_instance.error(f"Error checking crash log: {e}", exc_info=True)


# --- Enhanced Main Function ---
async def main():
    """Enhanced main function with comprehensive error handling and validation"""
    # Early logging setup
    logging.basicConfig(
        level=logging.INFO,
        format='[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S',
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler('startup.log', encoding='utf-8')
        ]
    )
    startup_logger = logging.getLogger('startup')

    # Validate critical configuration
    validation_errors = []
    global_config = config.get_global_config()

    if not global_config.get('DISCORD_TOKEN'):
        validation_errors.append("DISCORD_TOKEN not set.")

    if not global_config.get('DATABASE_URL'):
        validation_errors.append("DATABASE_URL not set.")

    if validation_errors:
        for error in validation_errors:
            startup_logger.critical(f"Configuration error: {error}")
        startup_logger.critical("Required configuration missing, cannot start bot.")
        sys.exit(1)

    # Enhanced intents configuration
    intents = discord.Intents.default()
    intents.message_content = True
    intents.messages = True
    intents.members = True
    intents.presences = True
    intents.guilds = True
    intents.reactions = True
    intents.voice_states = True

    # Create and configure bot
    bot = MyBot(command_prefix=config.COMMAND_PREFIX, intents=intents)
    bot_manager.set_bot(bot)

    try:
        startup_logger.info("Bot starting...")

        # Start bot with timeout
        await bot.start(config.DISCORD_TOKEN)

    except discord.LoginFailure:
        startup_logger.critical("Discord login failed - check your token")
        sys.exit(1)
    except discord.HTTPException as e:
        startup_logger.critical(f"Discord HTTP error: {e}")
        if "intents" in str(e).lower():
            startup_logger.critical("Check your bot's intent settings in the Discord Developer Portal.")
        sys.exit(1)
    except Exception as e:
        startup_logger.critical(f"Unexpected error during bot startup: {e}", exc_info=True)
        sys.exit(1)
    finally:
        # Ensure graceful shutdown
        if bot and hasattr(bot, 'graceful_shutdown'):
            try:
                await bot.graceful_shutdown()
            except Exception as e:
                startup_logger.error(f"Error during shutdown: {e}", exc_info=True)


# --- Enhanced Entry Point ---
if __name__ == "__main__":
    # Start Flask API server in a separate thread
    api_thread = Thread(target=run_api_server, daemon=False)
    api_thread.start()
    print(f"Bot API running on http://127.0.0.1:5001")

    try:
        # Handle different Python versions and event loop policies
        if sys.platform == 'win32':
            asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

        # Run the main Discord bot
        asyncio.run(main())

    except KeyboardInterrupt:
        print("\nBot manually stopped (KeyboardInterrupt).")
        bot = bot_manager.get_bot()
        if bot and hasattr(bot, 'logger'):
            bot.logger.info("Bot manually stopped (KeyboardInterrupt).")
    except Exception as e:
        print(f"Fatal error outside bot runtime: {e}", file=sys.stderr)
        logging.getLogger().critical(f"Fatal error outside bot runtime: {e}", exc_info=True)
        sys.exit(1)
    finally:
        # Ensure API thread cleanup
        if api_thread.is_alive():
            print("Waiting for API server to terminate...")
            # Give the API thread some time to finish
            api_thread.join(timeout=5)
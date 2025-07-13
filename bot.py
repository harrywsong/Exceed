# /home/hws/Exceed/bot.py

import discord
from discord.ext import commands, tasks
import os
import asyncio
import datetime
import aiohttp
import logging
import sys
import pathlib
import asyncpg  # Import for PostgreSQL async operations
import pytz  # Import pytz for timezone handling

# --- Flask API Imports ---
from flask import Flask, jsonify, request
from threading import Thread
import time  # For uptime calculation
import subprocess  # For git pull command
# --- End Flask API Imports ---

import utils.config as config
import utils.logger as logger_module  # This module contains get_logger and _configure_root_handlers
from utils import upload_to_drive  # Ensure this import is correct

# Define Eastern Timezone
EASTERN_TZ = pytz.timezone("US/Eastern")


# --- Database Functions (Moved from utils/database.py) ---
async def create_db_pool_in_bot():
    """Creates and returns a PostgreSQL connection pool using DATABASE_URL from environment variables."""
    try:
        database_url = os.getenv("DATABASE_URL")
        if not database_url:
            raise ValueError("DATABASE_URL 환경 변수가 설정되지 않았습니다.")

        pool = await asyncpg.create_pool(
            database_url,  # Pass the URL directly
            min_size=5,
            max_size=10,
            command_timeout=60
        )
        return pool
    except Exception as e:
        # Print directly as logger might not be fully set up yet during early startup
        print(f"❌ 환경 변수의 DATABASE_URL을 사용하여 데이터베이스 풀 생성 실패: {e}", file=sys.stderr)
        raise  # Re-raise to ensure bot doesn't start without DB


async def ensure_db_tables(pool):
    """Ensures necessary database tables exist."""
    async with pool.acquire() as conn:
        # Table for reaction roles
        await conn.execute("""
                           CREATE TABLE IF NOT EXISTS reaction_role_entries
                           (
                               id
                               SERIAL
                               PRIMARY
                               KEY,
                               message_id
                               BIGINT
                               NOT
                               NULL,
                               channel_id
                               BIGINT
                               NOT
                               NULL,
                               emoji
                               TEXT
                               NOT
                               NULL,
                               role_id
                               BIGINT
                               NOT
                               NULL,
                               UNIQUE
                           (
                               message_id,
                               emoji
                           )
                               );
                           """)
        # Table for user registrations (if not already handled by clanstats/registration cogs)
        await conn.execute("""
                           CREATE TABLE IF NOT EXISTS registrations
                           (
                               discord_id
                               BIGINT
                               PRIMARY
                               KEY,
                               riot_id
                               TEXT
                               NOT
                               NULL,
                               registered_at
                               TIMESTAMP
                               WITH
                               TIME
                               ZONE
                               DEFAULT
                               CURRENT_TIMESTAMP
                           );
                           """)
    print("✅ 데이터베이스 테이블이 확인되거나 생성되었습니다.")


# --- Flask API Setup ---
api_app = Flask(__name__)

# Suppress werkzeug INFO level messages for this Flask API app
# This needs to be done early to prevent werkzeug from adding its default handlers
werkzeug_logger = logging.getLogger('werkzeug')
werkzeug_logger.setLevel(logging.ERROR)  # Set level to ERROR to suppress INFO and WARNING
# Remove existing handlers from werkzeug logger to ensure no default output
if not werkzeug_logger.handlers:  # Only add if no handlers are present to avoid duplicates on reload
    for handler in list(werkzeug_logger.handlers):
        werkzeug_logger.removeHandler(handler)
    # You can optionally add a NullHandler if you want to completely silence it
    # werkzeug_logger.addHandler(logging.NullHandler())

# Store bot_instance globally or pass it, so API can access it
# This will be set in the main function
global bot_instance
bot_instance = None


@api_app.route('/status')
def bot_status():
    """Returns the current status of the bot."""
    if bot_instance and bot_instance.is_ready():
        uptime = datetime.datetime.now(datetime.timezone.utc) - bot_instance.start_time
        hours, remainder = divmod(int(uptime.total_seconds()), 3600)
        minutes, seconds = divmod(remainder, 60)
        uptime_str = f"{hours}h {minutes}m {seconds}s"

        latency_ms = round(bot_instance.latency * 1000, 2) if bot_instance.latency else 'N/A'

        return jsonify({
            "status": "Online",
            "uptime": uptime_str,
            "latency_ms": latency_ms,
            "guild_count": len(bot_instance.guilds),
            "user_count": len(bot_instance.users),
            "commands_used_today": bot_instance.total_commands_today,
            "message": "Bot is running and ready."
        })
    else:
        return jsonify({
            "status": "Offline",
            "uptime": "N/A",
            "latency_ms": "N/A",
            "guild_count": 0,
            "user_count": 0,
            "commands_used_today": 0,
            "error": "Bot is not ready or offline."
        })


@api_app.route('/command_stats')
def command_stats():
    """Returns command usage statistics."""
    if bot_instance:
        # Convert command_counts dictionary to a list of objects for JSON
        stats_list = []
        for cmd, count in bot_instance.command_counts.items():
            stats_list.append({"command_name": cmd, "usage_count": count})
        # Sort by usage_count descending
        stats_list.sort(key=lambda x: x['usage_count'], reverse=True)

        return jsonify({
            "status": "success",
            "command_stats": stats_list,
            "total_commands_today": bot_instance.total_commands_today
        })
    else:
        return jsonify({
            "status": "error",
            "error": "Bot instance not available."
        })


@api_app.route('/logs')
def get_logs():
    """Returns the last 500 lines of the bot's log file."""
    log_file_path = logger_module.LOG_FILE_PATH  # Use the path from logger_module
    try:
        if not os.path.exists(log_file_path):
            return jsonify({"status": "error", "error": "Log file not found."}), 404

        with open(log_file_path, 'r', encoding='utf-8', errors='ignore') as f:
            # Read all lines and get the last 500
            lines = f.readlines()
            last_500_lines = lines[-500:]  # Get the last 500 lines

        # Filter out specific Werkzeug access logs if they are still being written to the file
        # The logger setup in logger.py aims to prevent this, but as a fallback for the API.
        filtered_logs = [
            line.strip() for line in last_500_lines
            if "GET /status HTTP/1.1" not in line and
               "GET /logs HTTP/1.1" not in line and
               "GET /command_stats HTTP/1.1" not in line and
               "INFO....] [werkzeug]" not in line  # General werkzeug info logs
        ]

        return jsonify({"status": "success", "logs": filtered_logs})
    except Exception as e:
        print(f"Error reading log file: {e}", file=sys.stderr)
        return jsonify({"status": "error", "error": f"Failed to read log file: {e}"}), 500


@api_app.route('/control/<action>', methods=['POST'])
def control_bot_api(action):
    """Handles bot control actions like restart, reload cogs, update git."""
    if not bot_instance:
        return jsonify({"status": "error", "error": "Bot instance not available."}), 500

    if action == 'restart':
        bot_instance.logger.info("API 요청: 봇 재시작 중...")
        # This will stop the current bot and trigger systemd to restart it
        asyncio.run_coroutine_threadsafe(bot_instance.close(), bot_instance.loop)
        return jsonify({"status": "success", "message": "Bot restart initiated."})
    elif action == 'reload_cogs':
        bot_instance.logger.info("API 요청: 모든 Cog 재로드 중...")
        try:
            asyncio.run_coroutine_threadsafe(bot_instance.reload_all_cogs(), bot_instance.loop)
            return jsonify({"status": "success", "message": "All cogs reloaded successfully."})
        except Exception as e:
            bot_instance.logger.error(f"Cog 재로드 실패: {e}")
            return jsonify({"status": "error", "error": f"Failed to reload cogs: {e}"}), 500
    elif action == 'update_git':
        bot_instance.logger.info("API 요청: Git 업데이트 및 재시작 준비 중...")
        try:
            # Execute git pull in a non-blocking way if possible, or in a separate thread
            # For simplicity, using subprocess.run which is blocking, but in a separate thread.
            result = subprocess.run(['git', 'pull'], capture_output=True, text=True, cwd=os.getcwd())
            if result.returncode == 0:
                bot_instance.logger.info(f"Git pull 성공: {result.stdout.strip()}")
                # After successful pull, restart the bot to apply changes
                asyncio.run_coroutine_threadsafe(bot_instance.close(), bot_instance.loop)
                return jsonify(
                    {"status": "success", "message": "Git pull successful. Bot restarting to apply updates."})
            else:
                bot_instance.logger.error(f"Git pull 실패: {result.stderr.strip()}")
                return jsonify({"status": "error", "error": f"Git pull failed: {result.stderr.strip()}"}), 500
        except Exception as e:
            bot_instance.logger.error(f"Git 업데이트 중 오류 발생: {e}")
            return jsonify({"status": "error", "error": f"Error during git update: {e}"}), 500
    else:
        return jsonify({"status": "error", "error": "Invalid control action."}), 400


@api_app.route('/command/announce', methods=['POST'])
def send_announcement_api():
    """Sends an announcement to a specified channel."""
    if not bot_instance or not bot_instance.is_ready():
        return jsonify({"status": "error", "error": "Bot is not ready."}), 503

    data = request.get_json()
    channel_id = data.get('channel_id')
    message = data.get('message')

    if not channel_id or not message:
        return jsonify({"status": "error", "error": "Channel ID and message are required."}), 400

    try:
        channel = bot_instance.get_channel(int(channel_id))
        if not channel:
            return jsonify({"status": "error", "error": "Channel not found or bot does not have access."}), 404

        # Run the Discord send operation in the bot's event loop
        asyncio.run_coroutine_threadsafe(channel.send(message), bot_instance.loop)
        bot_instance.logger.info(f"API 요청: 채널 {channel_id}에 공지 전송 완료.")
        return jsonify({"status": "success", "message": "Announcement sent successfully."})
    except ValueError:
        return jsonify({"status": "error", "error": "Invalid channel ID format."}), 400
    except Exception as e:
        bot_instance.logger.error(f"공지 전송 실패: {e}")
        return jsonify({"status": "error", "error": f"Failed to send announcement: {e}"}), 500


@api_app.route('/api/config', methods=['GET'])
def get_bot_config():
    """
    Returns a subset of non-sensitive configuration values.
    This is a read-only endpoint for displaying current settings.
    """
    if not bot_instance:
        return jsonify({"status": "error", "error": "Bot instance not available."}), 500

    # Collect desired config values. Be careful not to expose sensitive tokens/keys.
    config_data = {
        "COMMAND_PREFIX": config.COMMAND_PREFIX,
        "LOG_CHANNEL_ID": config.LOG_CHANNEL_ID,
        "GUILD_ID": config.GUILD_ID,
        "AUTO_ROLE_IDS": config.AUTO_ROLE_IDS,
        "TICKET_CATEGORY_ID": config.TICKET_CATEGORY_ID,
        "STAFF_ROLE_ID": config.STAFF_ROLE_ID,
        "WELCOME_CHANNEL_ID": config.WELCOME_CHANNEL_ID,
        "GOODBYE_CHANNEL_ID": config.GOODBYE_CHANNEL_ID,
        "INTERVIEW_PUBLIC_CHANNEL_ID": config.INTERVIEW_PUBLIC_CHANNEL_ID,
        "INTERVIEW_PRIVATE_CHANNEL_ID": config.INTERVIEW_PRIVATE_CHANNEL_ID,
        "RULES_CHANNEL_ID": config.RULES_CHANNEL_ID,
        "ROLE_ASSIGN_CHANNEL_ID": config.ROLE_ASSIGN_CHANNEL_ID,
        "ANNOUNCEMENTS_CHANNEL_ID": config.ANNOUNCEMENTS_CHANNEL_ID,
        "ACCEPTED_ROLE_ID": config.ACCEPTED_ROLE_ID,
        "MEMBER_CHAT_CHANNEL_ID": config.MEMBER_CHAT_CHANNEL_ID,
        "CLAN_LEADERBOARD_CHANNEL_ID": config.CLAN_LEADERBOARD_CHANNEL_ID,
        "APPLICANT_ROLE_ID": config.APPLICANT_ROLE_ID,
        "GUEST_ROLE_ID": config.GUEST_ROLE_ID,
        "LOBBY_VOICE_CHANNEL_ID": config.LOBBY_VOICE_CHANNEL_ID,
        "TEMP_VOICE_CATEGORY_ID": config.TEMP_VOICE_CATEGORY_ID,
        "HISTORY_CHANNEL_ID": config.HISTORY_CHANNEL_ID,
        "TICKET_CHANNEL_ID": config.TICKET_CHANNEL_ID,
    }
    bot_instance.logger.info("API 요청: 봇 설정 조회 완료.")
    return jsonify({"status": "success", "config": config_data})


@api_app.route('/api/reaction_roles', methods=['GET'])
async def get_reaction_roles():
    """
    Fetches all reaction role entries from the database.
    """
    if not bot_instance or not bot_instance.pool:
        return jsonify({"status": "error", "error": "봇 또는 데이터베이스가 준비되지 않았습니다."}), 503

    try:
        reaction_roles_cog = bot_instance.get_cog('ReactionRoles')
        if not reaction_roles_cog:
            return jsonify({"status": "error", "error": "ReactionRoles Cog가 로드되지 않았습니다."}), 500

        # Run the async database fetch in the bot's event loop
        entries = await asyncio.wrap_future(
            asyncio.run_coroutine_threadsafe(
                reaction_roles_cog.get_all_reaction_role_entries_db(), bot_instance.loop
            )
        )
        bot_instance.logger.info("API 요청: 리액션 역할 조회 완료.")
        return jsonify({"status": "success", "reaction_roles": entries})
    except Exception as e:
        bot_instance.logger.error(f"리액션 역할 조회 실패: {e}", exc_info=True)
        return jsonify({"status": "error", "error": f"리액션 역할 가져오기 실패: {e}"}), 500


@api_app.route('/api/reaction_roles/add', methods=['POST'])
async def add_reaction_role():
    """
    Adds a new reaction role entry to the database.
    Requires message_id, channel_id, emoji, and role_id in the request body.
    """
    if not bot_instance or not bot_instance.pool:
        return jsonify({"status": "error", "error": "봇 또는 데이터베이스가 준비되지 않았습니다."}), 503

    data = request.get_json()
    message_id = data.get('message_id')
    channel_id = data.get('channel_id')
    emoji = data.get('emoji')
    role_id = data.get('role_id')

    if not all([message_id, channel_id, emoji, role_id]):
        return jsonify({"status": "error", "error": "필수 필드 (메시지 ID, 채널 ID, 이모지, 역할 ID)가 누락되었습니다."}), 400

    try:
        reaction_roles_cog = bot_instance.get_cog('ReactionRoles')
        if not reaction_roles_cog:
            return jsonify({"status": "error", "error": "ReactionRoles Cog가 로드되지 않았습니다."}), 500

        await asyncio.wrap_future(
            asyncio.run_coroutine_threadsafe(
                reaction_roles_cog.add_reaction_role_entry_db(
                    int(message_id), int(channel_id), emoji, int(role_id)
                ), bot_instance.loop
            )
        )
        bot_instance.logger.info(f"API 요청: 리액션 역할 추가 완료 (메시지: {message_id}, 이모지: {emoji}, 역할: {role_id}).")
        return jsonify({"status": "success", "message": "리액션 역할이 성공적으로 추가되었습니다."})
    except ValueError:
        return jsonify({"status": "error", "error": "잘못된 ID 형식입니다. 메시지 ID, 채널 ID, 역할 ID는 정수여야 합니다."}), 400
    except asyncpg.exceptions.UniqueViolationError:
        return jsonify({"status": "error", "error": "이 메시지 ID와 이모지를 가진 리액션 역할이 이미 존재합니다."}), 409
    except Exception as e:
        bot_instance.logger.error(f"리액션 역할 추가 실패: {e}", exc_info=True)
        return jsonify({"status": "error", "error": f"리액션 역할 추가 실패: {e}"}), 500


@api_app.route('/api/reaction_roles/remove', methods=['POST'])
async def remove_reaction_role():
    """
    Removes a reaction role entry from the database.
    Requires message_id and emoji in the request body.
    """
    if not bot_instance or not bot_instance.pool:
        return jsonify({"status": "error", "error": "봇 또는 데이터베이스가 준비되지 않았습니다."}), 503

    data = request.get_json()
    message_id = data.get('message_id')
    emoji = data.get('emoji')

    if not all([message_id, emoji]):
        return jsonify({"status": "error", "error": "필수 필드 (메시지 ID, 이모지)가 누락되었습니다."}), 400

    try:
        reaction_roles_cog = bot_instance.get_cog('ReactionRoles')
        if not reaction_roles_cog:
            return jsonify({"status": "error", "error": "ReactionRoles Cog가 로드되지 않았습니다."}), 500

        success = await asyncio.wrap_future(
            asyncio.run_coroutine_threadsafe(
                reaction_roles_cog.remove_reaction_role_entry_db(
                    int(message_id), emoji
                ), bot_instance.loop
            )
        )
        if success:
            bot_instance.logger.info(f"API 요청: 리액션 역할 제거 완료 (메시지: {message_id}, 이모지: {emoji}).")
            return jsonify({"status": "success", "message": "리액션 역할이 성공적으로 제거되었습니다."})
        else:
            return jsonify({"status": "error", "message": "리액션 역할을 찾을 수 없습니다."}), 404
    except ValueError:
        return jsonify({"status": "error", "error": "잘못된 ID 형식입니다. 메시지 ID는 정수여야 합니다."}), 400
    except Exception as e:
        bot_instance.logger.error(f"리액션 역할 제거 실패: {e}", exc_info=True)
        return jsonify({"status": "error", "error": f"리액션 역할 제거 실패: {e}"}), 500


def run_api_server():
    """Runs the Flask API server for the bot in a separate thread."""
    # Use a different port than the UI Flask app (5000)
    api_app.run(host='127.0.0.1', port=5001, debug=False)  # Set debug=False for this API server


# --- End Flask API Setup ---


class MyBot(commands.Bot):
    def __init__(self, command_prefix, intents):
        super().__init__(command_prefix=command_prefix, intents=intents)
        self.start_time = datetime.datetime.now(datetime.timezone.utc)
        self.pool = None  # Database connection pool
        self.session = aiohttp.ClientSession()  # For HTTP requests
        self.command_counts = {}  # For command usage stats
        self.total_commands_today = 0  # Track total commands for the day
        # Initialize a basic logger immediately to ensure it always exists
        self.logger = logging.getLogger('discord')  # This is a standard Python logger

    async def setup_hook(self):
        # Initialize database pool
        try:
            self.pool = await create_db_pool_in_bot()
            self.logger.info("✅ 데이터베이스 연결 풀이 성공적으로 생성되었습니다.")
            await ensure_db_tables(self.pool)  # Ensure tables exist
        except Exception as e:
            self.logger.critical(f"❌ 데이터베이스 풀 생성 실패: {e}", exc_info=True)
            # Exit if DB connection fails, as bot won't function correctly
            sys.exit(1)

        # Configure the main logger using get_logger from utils.logger
        # This runs after the bot is ready and has access to self (the bot instance)
        try:
            # Call _configure_root_handlers to set up all handlers, including DiscordHandler
            # Pass the bot instance here as its loop is now available for DiscordHandler
            logger_module._configure_root_handlers(
                bot=self,
                discord_log_channel_id=config.LOG_CHANNEL_ID
            )
            # Re-assign self.logger to the root logger which now has all handlers
            self.logger = logging.getLogger('기본 로그')  # Get the root logger instance
            self.logger.info("✅ 봇 로거가 성공적으로 설정되었습니다.")
        except Exception as e:
            self.logger.critical(f"❌ 로거 설정 중 심각한 오류 발생: {e}", exc_info=True)
            # Continue with basic logger if configuration fails, but log it.

        # Load cogs
        initial_extensions = [
            'cogs.autoguest',
            'cogs.clanstats',
            'cogs.clear_messages',
            'cogs.interview',
            'cogs.leaderboard',
            'cogs.reaction_roles',  # This cog will be modified to use DB
            'cogs.registration',
            'cogs.scraper',
            'cogs.ticket',
            'cogs.voice',
            'cogs.welcomegoodbye',
        ]
        for ext in initial_extensions:
            try:
                if ext == 'cogs.autoguest':
                    # Removed 'extras' argument as it's not supported by load_extension.
                    # The cogs.autoguest module should import config and use AUTO_ROLE_IDS directly.
                    await self.load_extension(ext)
                else:
                    await self.load_extension(ext)
                self.logger.info(f"✅ Cog 로드됨: {ext}")
            except commands.ExtensionAlreadyLoaded:
                self.logger.warning(f"⚠️ Cog '{ext}'는 이미 로드되어 있습니다. 건너_.")
            except commands.ExtensionFailed as e:
                self.logger.error(f"❌ Cog '{ext}' 로드 실패 (설정 오류 또는 내부 오류): {e}", exc_info=True)
            except commands.ExtensionNotFound:
                self.logger.error(f"❌ Cog '{ext}'를 찾을 수 없습니다. 파일 경로를 확인하세요.")
            except Exception as e:
                self.logger.critical(f"❌ Cog '{ext}' 로드 중 예상치 못한 오류 발생: {e}", exc_info=True)

        # Sync slash commands globally (or to a specific guild for faster testing)
        try:
            synced = await self.tree.sync()  # Sync globally or self.tree.sync(guild=discord.Object(id=YOUR_GUILD_ID))
            self.logger.info(f"✅ 슬래시 명령어 {len(synced)}개 동기화 완료.")
        except Exception as e:
            self.logger.error(f"❌ 슬래시 명령어 동기화 실패: {e}", exc_info=True)

    async def reload_all_cogs(self):
        """Reloads all currently loaded cogs."""
        for ext in list(self.extensions.keys()):  # Iterate over a copy
            try:
                await self.reload_extension(ext)
                self.logger.info(f"🔄 Cog 재로드됨: {ext}")
            except commands.ExtensionNotLoaded:
                self.logger.warning(f"⚠️ Cog '{ext}'가 로드되지 않았으므로 재로드할 수 없습니다.")
            except commands.ExtensionFailed as e:
                self.logger.error(f"❌ Cog '{ext}' 재로드 실패: {e}", exc_info=True)
            except Exception as e:
                self.logger.critical(f"❌ Cog '{ext}' 재로드 중 예상치 못한 오류 발생: {e}", exc_info=True)

    async def on_ready(self):
        """Event that fires when the bot is ready."""
        self.logger.info(f"--- 봇 로그인 완료: {self.user} (ID: {self.user.id}) ---")
        self.logger.info(f"봇이 다음 길드에 연결되었습니다:")
        for guild in self.guilds:
            self.logger.info(f"- {guild.name} (ID: {guild.id})")
        self.logger.info(f"현재 핑: {round(self.latency * 1000)}ms")

        # Set bot status
        await self.change_presence(activity=discord.Game(name="클랜원 모집 중!"))

        # Set the global bot_instance for the Flask API
        global bot_instance
        bot_instance = self
        self.logger.info("Flask API에서 봇 인스턴스를 사용할 수 있습니다.")

        # Ensure crash log handling runs after logger is fully set up
        await self.loop.run_in_executor(None, check_crash_log_and_handle, self.logger)

        # Start the daily log upload task
        self.daily_log_upload.start()
        self.logger.info("일일 로그 업로드 작업을 시작했습니다.")

        # Upload any un-uploaded rotated logs from previous days on startup
        await self.loop.run_in_executor(None, upload_daily_logs_on_startup, self.logger)

    async def on_command_completion(self, context):
        """Event that fires when a traditional prefix command is successfully completed."""
        command_name = context.command.name
        self.command_counts[command_name] = self.command_counts.get(command_name, 0) + 1
        self.total_commands_today += 1
        self.logger.info(f"사용자 {context.author}님이 명령어 '{command_name}'을(를) 사용했습니다.")

    @commands.Cog.listener()
    async def on_app_command_completion(self, interaction: discord.Interaction, command: discord.app_commands.Command):
        """Event that fires when a slash command is successfully completed."""
        command_name = command.name
        # For slash commands, interaction.user is the user who invoked the command
        user_name = interaction.user.display_name if interaction.user else "Unknown User"
        user_id = interaction.user.id if interaction.user else "Unknown ID"

        self.command_counts[command_name] = self.command_counts.get(command_name, 0) + 1
        self.total_commands_today += 1
        self.logger.info(f"사용자 {user_name} ({user_id})님이 슬래시 명령어 '/{command_name}'을(를) 사용했습니다.")

    async def on_command_error(self, context, error):
        """Global command error handler."""
        if isinstance(error, commands.CommandNotFound):
            # Silently ignore if command not found, or send ephemeral message
            # await context.send("알 수 없는 명령어입니다. `/`를 눌러 사용 가능한 명령어를 확인하세요.", ephemeral=True)
            return
        if isinstance(error, commands.MissingPermissions):
            await context.send(f"❌ 이 명령어를 실행할 권한이 없습니다: {error}", ephemeral=True)
        elif isinstance(error, commands.MissingRequiredArgument):
            await context.send(f"❌ 필요한 인수가 누락되었습니다: {error}\n명령어 사용법을 확인해주세요.", ephemeral=True)
        elif isinstance(error, commands.BadArgument):
            await context.send(f"❌ 잘못된 인수입니다: {error}", ephemeral=True)
        elif isinstance(error, commands.NoPrivateMessage):
            await context.send("❌ 이 명령어는 DM에서 사용할 수 없습니다.", ephemeral=True)
        else:
            self.logger.error(f"명령어 '{context.command}' 실행 중 예상치 못한 오류 발생: {error}", exc_info=True)
            await context.send("❌ 명령어 실행 중 예상치 못한 오류가 발생했습니다. 관리자에게 문의해주세요.", ephemeral=True)

    @tasks.loop(time=datetime.time(0, 0, 0, tzinfo=EASTERN_TZ))  # 12 AM Eastern Time
    async def daily_log_upload(self):
        """
        Uploads the previous day's log file to Google Drive at 12 AM Eastern.
        """
        await self.bot.wait_until_ready()  # Ensure bot is ready before performing operations

        # Get yesterday's date in Eastern Time
        now_eastern = datetime.datetime.now(EASTERN_TZ)
        yesterday_eastern = now_eastern - datetime.timedelta(days=1)
        yesterday_date_str = yesterday_eastern.strftime("%Y-%m-%d")

        # Construct the path to yesterday's rotated log file
        # TimedRotatingFileHandler renames log.log to log.log.YYYY-MM-DD at midnight
        log_file_to_upload = logger_module.LOG_FILE_PATH.parent / f"log.log.{yesterday_date_str}"

        if log_file_to_upload.exists():
            self.logger.info(f"일일 로그 업로드 시작: {log_file_to_upload.name}")
            try:
                # Use the updated upload_file function
                upload_to_drive.upload_file(str(log_file_to_upload), f"daily_log_{log_file_to_upload.name}")
                self.logger.info(f"✅ 일일 로그 '{log_file_to_upload.name}' Google Drive에 성공적으로 업로드 및 삭제되었습니다.")
            except Exception as e:
                self.logger.error(f"❌ 일일 로그 '{log_file_to_upload.name}' 업로드 실패: {e}", exc_info=True)
        else:
            self.logger.info(f"일일 로그 파일 '{log_file_to_upload.name}'을(를) 찾을 수 없습니다. (어제 로그 없음 또는 이미 처리됨)")


# --- Crash Log Handling ---
CRASH_LOG_DIR = pathlib.Path(__file__).parent.parent / "logs"
CRASH_LOG_FILE = CRASH_LOG_DIR / "crash_log.txt"  # This captures sys.stderr output
CRASH_LOG_DIR.mkdir(parents=True, exist_ok=True)

# Store original stderr
original_stderr = sys.stderr


def check_crash_log_and_handle(logger_instance: logging.Logger):
    """
    Checks for crash log files (sys.stderr output and main log renamed on crash)
    and attempts to upload them to Google Drive.
    This runs in a separate thread/executor to avoid blocking the bot's main loop.
    """
    # 1. Handle the sys.stderr crash log (crash_log.txt)
    if CRASH_LOG_FILE.exists() and CRASH_LOG_FILE.stat().st_size > 0:  # Check if it's not empty
        logger_instance.warning("⚠️ 이전 봇 충돌 (stderr) 로그 파일이 감지되었습니다. Google Drive에 업로드 중...")
        try:
            # Use datetime.datetime.now()
            upload_to_drive.upload_file(str(CRASH_LOG_FILE),
                                        f"stderr_crash_log_{datetime.datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}.log")
            logger_instance.info("✅ stderr 충돌 로그 파일이 성공적으로 업로드 및 삭제되었습니다.")
        except Exception as e:
            logger_instance.error(f"❌ stderr 충돌 로그 파일 업로드 또는 삭제 실패: {e}", exc_info=True)
    else:
        logger_instance.info("처리할 보류 중인 stderr 충돌 로그 파일이 없습니다.")

    # 2. Handle main log files renamed on crash (log.log.CRASH-YYYY-MM-DD_HH-MM-SS)
    log_dir = logger_module.LOG_FILE_PATH.parent
    for filename in os.listdir(log_dir):
        if filename.startswith("log.log.CRASH-") and filename.endswith(".log"):
            crash_log_path = log_dir / filename
            if crash_log_path.exists() and crash_log_path.stat().st_size > 0:  # Check if it's not empty
                logger_instance.warning(f"⚠️ 이전 메인 로그 충돌 파일 '{filename}'이(가) 감지되었습니다. Google Drive에 업로드 중...")
                try:
                    # Upload with its original crash-specific name
                    upload_to_drive.upload_file(str(crash_log_path), filename)
                    logger_instance.info(f"✅ 메인 로그 충돌 파일 '{filename}'이(가) Google Drive에 성공적으로 업로드 및 삭제되었습니다.")
                except Exception as e:
                    logger_instance.error(f"❌ 메인 로그 충돌 파일 '{filename}' 업로드 또는 삭제 실패: {e}", exc_info=True)
            else:
                logger_instance.info(f"비어 있거나 유효하지 않은 메인 로그 충돌 파일 '{filename}'을(를) 건너뜁니다.")


# --- End Crash Log Handling ---

def upload_daily_logs_on_startup(logger_instance: logging.Logger):
    """
    Checks for and uploads any rotated log files from previous days that might not have been uploaded.
    This runs on bot startup.
    """
    log_dir = logger_module.LOG_FILE_PATH.parent
    today_date_str = datetime.datetime.now(EASTERN_TZ).strftime("%Y-%m-%d")

    logger_instance.info("시작 시 이전 일일 로그 파일 확인 및 업로드 중...")
    for filename in os.listdir(log_dir):
        # Exclude current log.log, crash_log.txt, and any log.log.CRASH-* files (as they are handled separately)
        if filename == "log.log" or filename == "crash_log.txt" or filename.startswith("log.log.CRASH-"):
            continue

        if filename.startswith("log.log.") and filename.endswith(".log"):
            # Extract date from filename (e.g., "log.log.2023-10-26")
            file_date_str = filename.replace("log.log.", "").replace(".log", "")

            # Only process if it's a valid date string and not today's log
            try:
                file_date = datetime.datetime.strptime(file_date_str, "%Y-%m-%d").date()
                if file_date < datetime.datetime.now(EASTERN_TZ).date():  # Only upload logs older than today
                    local_file_path = log_dir / filename
                    drive_file_name = f"daily_log_{filename}"
                    logger_instance.info(f"시작 시 이전 일일 로그 파일 업로드: {filename}")
                    try:
                        upload_to_drive.upload_file(str(local_file_path), drive_file_name)
                        logger_instance.info(f"✅ 시작 시 '{filename}' Google Drive에 성공적으로 업로드 및 삭제되었습니다.")
                    except Exception as e:
                        logger_instance.error(f"❌ 시작 시 '{filename}' 업로드 실패: {e}", exc_info=True)
            except ValueError:
                # Ignore files that don't match the date format
                logger_instance.debug(f"로그 파일 '{filename}'이(가) 예상 날짜 형식과 일치하지 않아 건너뜁니다.")


async def main():
    # Define intents required by your bot
    intents = discord.Intents.default()
    intents.members = True  # Required for on_member_join, member caching
    intents.message_content = True  # Required to read message content (for prefix commands)
    intents.presences = True  # Required for presence updates (e.g., active users count)

    # Create bot instance
    global bot_instance  # Declare global to assign to it
    bot_instance = MyBot(command_prefix=config.COMMAND_PREFIX, intents=intents)

    # For very early startup, before setup_hook runs, configure a basic console logger.
    # The full logger configuration with DiscordHandler will happen in setup_hook.
    # This ensures logging is available for critical errors even before the bot is ready.
    logging.basicConfig(level=logging.INFO,
                        format='[%(asctime)s] [%(levelname)s] [%(name)s] {message}',
                        datefmt='%Y-%m-%d %H:%M:%S',
                        style='{')
    # Assign this basic logger to bot_instance.logger for early use
    bot_instance.logger = logging.getLogger('초기 로거')

    # Check for Discord Token
    TOKEN = config.DISCORD_TOKEN
    if not TOKEN:
        # Use the bot's logger if available, otherwise print
        if hasattr(bot_instance, 'logger') and bot_instance.logger is not None:
            bot_instance.logger.critical("DISCORD_TOKEN이 config.py에 설정되지 않았습니다. 종료합니다.")
        else:
            print("CRITICAL: DISCORD_TOKEN이 config.py에 설정되지 않았습니다. 종료합니다.", file=sys.stderr)
        sys.exit(1)

    # Redirect sys.stderr to the crash log file before starting the bot
    # This ensures any unhandled exceptions are written to the crash log
    try:
        sys.stderr = open(CRASH_LOG_FILE, 'a', encoding='utf-8')
    except Exception as e:
        print(f"❌ 충돌 로그 파일로 stderr 리디렉션 실패: {e}", file=original_stderr)
        # Revert to original stderr if redirection fails
        sys.stderr = original_stderr

    try:
        # Start the bot
        await bot_instance.start(TOKEN)
    except discord.HTTPException as e:
        # Use the bot's logger
        if hasattr(bot_instance, 'logger') and bot_instance.logger is not None:
            bot_instance.logger.critical(f"HTTP 예외: {e} - 봇 토큰이 올바르고 인텐트가 활성화되었는지 확인하세요.", exc_info=True)
        else:
            print(f"CRITICAL: HTTP 예외: {e} - 봇 토큰이 올바르고 인텐트가 활성화되었는지 확인하세요.", file=sys.stderr)
    except Exception as e:
        # Use the bot's logger
        if hasattr(bot_instance, 'logger') and bot_instance.logger is not None:
            bot_instance.logger.critical(f"봇 런타임 중 처리되지 않은 오류 발생: {e}", exc_info=True)
        else:
            print(f"CRITICAL: 봇 런타임 중 처리되지 않은 오류 발생: {e}", file=sys.stderr)
    finally:
        # Ensure bot_instance.logger is checked before use in finally block
        if hasattr(bot_instance, 'logger') and bot_instance.logger is not None:
            bot_instance.logger.info("봇이 중지되었습니다.")
        else:
            print("INFO: 봇이 중지되었습니다 (로거 초기화 실패).", file=sys.stderr)
        # Ensure bot_instance is not None before calling close
        if bot_instance:
            await bot_instance.close()
        # Restore original stderr
        if sys.stderr != original_stderr:
            sys.stderr.close()
            sys.stderr = original_stderr


if __name__ == "__main__":
    # Start the Flask API in a separate thread
    # This ensures the API runs concurrently with the Discord bot.
    api_thread = Thread(target=run_api_server)
    api_thread.daemon = True  # Allow main program to exit even if thread is running
    api_thread.start()
    print(f"Existing Bot API running on http://127.0.0.1:5001")

    try:
        # Run the main Discord bot asynchronous loop
        asyncio.run(main())
    except KeyboardInterrupt:
        # Handle graceful shutdown on Ctrl+C
        if bot_instance:
            if hasattr(bot_instance, 'logger') and bot_instance.logger is not None:
                bot_instance.logger.info("봇이 수동으로 중지되었습니다 (KeyboardInterrupt).")
            else:
                print("INFO: 봇이 수동으로 중지되었습니다 (로거 초기화 실패, KeyboardInterrupt).", file=sys.stderr)
        else:
            print("INFO: 봇이 수동으로 중지되었습니다 (KeyboardInterrupt).", file=sys.stderr)
    except Exception as e:
        # Catch any unhandled exceptions during the bot's main run
        # Use a basic logger or print, as bot_instance.logger might not be fully initialized
        logging.getLogger().critical(f"봇 런타임 외부에서 치명적인 오류 발생: {e}", exc_info=True)
        # Attempt to use bot_instance's logger if it exists
        if 'bot_instance' in locals() and hasattr(bot_instance, 'logger') and bot_instance.logger is not None:
            bot_instance.logger.critical(f"봇 런타임 외부에서 치명적인 오류 발생 (재시도): {e}", exc_info=True)


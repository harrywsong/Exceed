# /home/hws/Exceed/bot.py

import discord
from discord import app_commands
from discord.ext import commands, tasks
import os
import asyncio
import datetime
import aiohttp
import logging
import sys
import pathlib
import asyncpg # Import for PostgreSQL async operations

# --- Flask API Imports ---
from flask import Flask, jsonify, request
from threading import Thread
import time # For uptime calculation
import subprocess # For git pull command
from flask_cors import CORS # Import CORS for cross-origin requests
# --- End Flask API Imports ---

import utils.config as config
import utils.logger as logger_module # Good: using an alias for clarity
from utils import upload_to_drive

# --- Database Functions ---
async def create_db_pool_in_bot():
    """Creates and returns a PostgreSQL connection pool using DATABASE_URL from environment variables."""
    try:
        database_url = os.getenv("DATABASE_URL")
        if not database_url:
            raise ValueError("DATABASE_URL 환경 변수가 설정되지 않았습니다.")

        pool = await asyncpg.create_pool(
            database_url, # Pass the URL directly
            min_size=5,
            max_size=10,
            command_timeout=60
        )
        return pool
    except Exception as e:
        # Print directly as logger might not be fully set up yet during early startup
        print(f"❌ 환경 변수의 DATABASE_URL을 사용하여 데이터베이스 풀 생성 실패: {e}")
        raise # Re-raise to ensure bot doesn't start without DB

async def ensure_db_tables_exist(pool):
    """Ensures necessary database tables (like command_usage) exist."""
    async with pool.acquire() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS command_usage (
                command_name TEXT PRIMARY KEY,
                usage_count INTEGER DEFAULT 0,
                last_used TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
            );
        """)
    print("✅ 데이터베이스 테이블 확인 및 생성 완료.")


# --- Discord Bot Setup ---
class MyBot(commands.Bot):
    def __init__(self):
        super().__init__(
            command_prefix=commands.when_mentioned_or("!"),
            intents=discord.Intents.all(), # discord.Intents.all() includes members and presences
            sync_commands_debug=True
        )
        self.initial_extensions = [
            'cogs.welcomegoodbye',
            'cogs.interview',
            'cogs.ticket',
            'cogs.clanstats',
            'cogs.registration',
            'cogs.autoguest',
            'cogs.voice',
            'cogs.scraper',
            'cogs.clear_messages',
            'cogs.reaction_roles',
            'cogs.leaderboard',
        ]
        self.session = aiohttp.ClientSession()
        self.ready_event = asyncio.Event()
        self.log_channel_id = config.LOG_CHANNEL_ID
        self.pool = None
        self.bot_start_time = time.time() # Store bot's start time for uptime
        # self.commands_executed_today is now managed by DB for persistence

    async def setup_hook(self):
        # Ensure DB tables exist before loading cogs that might use them
        if self.pool:
            await ensure_db_tables_exist(self.pool)

        for ext in self.initial_extensions:
            try:
                await self.load_extension(ext)
                logger_module.root_logger.info(f"코그 로드 완료: {ext.split('.')[-1]}.py")
            except Exception as e:
                # FIX 1: Use root_logger here, as self.logger might not be assigned yet
                logger_module.root_logger.error(f"확장 로드 실패 {ext}: {e}", exc_info=True)

        try:
            await self.tree.sync()
            # FIX 1: Use root_logger here as well for consistency during startup
            logger_module.root_logger.info("슬래시 명령어 동기화 완료.")
        except Exception as e:
            # FIX 1: Use root_logger here as well
            logger_module.root_logger.error(f"슬래시 명령어 동기화 실패: {e}", exc_info=True)

    async def on_ready(self):
        self.ready_event.set()
        # FIX 1: Use root_logger here for the initial "ready" message
        logger_module.root_logger.info(f"{self.user} (ID: {self.user.id}) 로 로그인 성공")

        # The DiscordHandler's log sending task is now started in main()
        # after _configure_root_handlers, so this block is no longer needed here.
        # It's better to start the task immediately after adding the handler
        # to ensure it's always running on the latest handler instance.

    async def close(self):
        if self.pool: # Ensure pool is closed when bot closes
            await self.pool.close()
            self.logger.info("✅ 데이터베이스 풀이 닫혔습니다.")
        await self.session.close()
        # Ensure all log handlers are properly closed on shutdown
        for handler in logging.getLogger().handlers:
            if hasattr(handler, 'flush'):
                handler.flush()
            if hasattr(handler, 'close'):
                handler.close()
        await super().close()
        self.logger.info("봇이 종료되었습니다.")

    async def on_command_error(self, ctx, error):
        # Log command errors, but don't increment usage for errors unless desired
        if not isinstance(error, commands.CommandNotFound):
            self.logger.error(f"명령어 '{ctx.command}' 실행 중 오류 발생: {error}", exc_info=True)
            await ctx.send(f"오류가 발생했습니다: {error}")
        else:
            # For CommandNotFound, we might still want to count it as an attempt,
            # but for now, we'll only count recognized commands in on_command.
            return # Do not send error message for CommandNotFound

    async def on_app_command_completion(self, interaction: discord.Interaction, command: app_commands.Command):
        """Hook for when an application command (slash command) is successfully completed."""
        # This is a better place to count successful command executions for slash commands
        command_name = command.name
        if self.pool:
            try:
                async with self.pool.acquire() as conn:
                    await conn.execute("""
                        INSERT INTO command_usage (command_name, usage_count, last_used)
                        VALUES ($1, 1, CURRENT_TIMESTAMP)
                        ON CONFLICT (command_name) DO UPDATE SET
                            usage_count = command_usage.usage_count + 1,
                            last_used = CURRENT_TIMESTAMP;
                    """, command_name)
                    self.logger.debug(f"Command '{command_name}' usage updated in DB.")
            except Exception as e:
                self.logger.error(f"Failed to update command usage for '{command_name}' in DB: {e}", exc_info=True)
        else:
            self.logger.warning(f"Database pool not available, cannot log command usage for '{command_name}'.")

    async def on_command(self, ctx):
        """Hook for when a prefix command is successfully completed."""
        command_name = ctx.command.name
        if self.pool:
            try:
                async with self.pool.acquire() as conn:
                    await conn.execute("""
                        INSERT INTO command_usage (command_name, usage_count, last_used)
                        VALUES ($1, 1, CURRENT_TIMESTAMP)
                        ON CONFLICT (command_name) DO UPDATE SET
                            usage_count = command_usage.usage_count + 1,
                            last_used = CURRENT_TIMESTAMP;
                    """, command_name)
                    self.logger.debug(f"Command '{command_name}' usage updated in DB.")
            except Exception as e:
                self.logger.error(f"Failed to update command usage for '{command_name}' in DB: {e}", exc_info=True)
        else:
            self.logger.warning(f"Database pool not available, cannot log command usage for '{command_name}'.")


# --- Flask API for the Management UI ---
api_app = Flask(__name__)
CORS(api_app) # Enable CORS for all routes

# --- Helper to calculate uptime ---
def get_uptime_string(start_timestamp):
    delta = datetime.timedelta(seconds=int(time.time() - start_timestamp))
    total_seconds = int(delta.total_seconds())
    days, remainder = divmod(total_seconds, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes, seconds = divmod(remainder, 60)

    uptime_parts = []
    if days > 0:
        uptime_parts.append(f"{days}d")
    if hours > 0:
        uptime_parts.append(f"{hours}h")
    if minutes > 0:
        uptime_parts.append(f"{minutes}m")
    if seconds > 0 or not uptime_parts: # Always show seconds if nothing else, or if it's 0s
        uptime_parts.append(f"{seconds}s")
    return " ".join(uptime_parts) if uptime_parts else "0s"


@api_app.route('/status', methods=['GET'])
def get_bot_status():
    """
    API Endpoint: GET /status
    봇의 현재 작동 상태와 주요 통계를 반환합니다.
    """
    global bot_instance

    status = "Offline"
    latency = "N/A"
    guild_count = 0
    user_count = 0
    uptime_str = "N/A"
    commands_today = 0 # This will now be fetched from DB for current day

    if bot_instance and bot_instance.is_ready():
        status = "Online"
        latency = round(bot_instance.latency * 1000)
        guild_count = len(bot_instance.guilds)
        user_count = len(bot_instance.users) # This relies on Intents.members and Intents.presences
        uptime_str = get_uptime_string(bot_instance.bot_start_time)
        # Fetch commands executed today from DB (reset daily by DB logic or manual clear)
        # For simplicity, we'll just return the total count for now,
        # A more robust solution would involve a 'daily_commands' table or date filtering.
        # For now, let's just make sure it's not 'N/A'
        commands_today = "Loading..." # Will be fetched via /command_stats for the chart

    return jsonify({
        "status": status,
        "uptime": uptime_str,
        "latency_ms": latency,
        "guild_count": guild_count,
        "user_count": user_count,
        "commands_used_today": commands_today # This will be updated by /command_stats
    })

@api_app.route('/command/announce', methods=['POST'])
def api_announce():
    """
    API Endpoint: POST /command/announce
    봇이 공지 메시지를 보내도록 트리거합니다.
    """
    data = request.json
    channel_id = data.get('channel_id')
    message = data.get('message')

    if not channel_id or not message:
        return jsonify({"status": "error", "error": "Missing channel_id or message"}), 400

    try:
        channel_id = int(channel_id)
    except ValueError:
        return jsonify({"status": "error", "error": "Invalid channel_id. Must be an integer."}), 400

    async def _send_announcement():
        """비동기 함수로 discord.py를 통해 메시지를 보냅니다."""
        global bot_instance
        if not bot_instance or not bot_instance.is_ready():
            return {"status": "error", "error": "Bot is not ready or offline."}

        channel = bot_instance.get_channel(channel_id)
        if channel:
            try:
                await channel.send(f"**UI에서 공지:** {message}")
                return {"status": "success", "message": "공지가 성공적으로 전송되었습니다."}
            except discord.Forbidden:
                return {"status": "error", "error": "봇이 해당 채널에 메시지를 보낼 권한이 없습니다."}
            except Exception as e:
                bot_instance.logger.error(f"Discord API 오류 (공지): {e}", exc_info=True)
                return {"status": "error", "error": f"Discord API 오류: {e}"}
        else:
            return {"status": "error", "error": "채널을 찾을 수 없습니다. ID가 올바르고 봇이 길드에 있는지 확인하세요."}

    try:
        # Use bot_instance.loop to run coroutine in bot's thread
        future = asyncio.run_coroutine_threadsafe(_send_announcement(), bot_instance.loop)
        result = future.result(timeout=15) # Wait for the result with a timeout
        return jsonify(result)
    except asyncio.TimeoutError:
        return jsonify({"status": "error", "error": "봇이 공지에 제시간에 응답하지 않았습니다."}), 500
    except Exception as e:
        bot_instance.logger.error(f"공지 예약 중 오류 발생: {e}", exc_info=True)
        return jsonify({"status": "error", "error": f"공지 예약 실패: {e}"}), 500


@api_app.route('/control/restart', methods=['POST'])
def api_restart():
    """
    API Endpoint: POST /control/restart
    봇 종료를 시작합니다. 실제 재시작을 위해서는 외부 프로세스 관리자가 필요합니다.
    """
    global bot_instance
    print("UI에서 재시작 명령을 받았습니다. 봇 종료를 시작합니다...")
    # FIX 1: Use root_logger here as well for initial messages
    logger_module.root_logger.info("UI에서 재시작 명령을 받았습니다. 봇 종료를 시작합니다...")

    async def _shutdown_bot():
        if bot_instance:
            await bot_instance.close() # This will close the Discord connection and session

    try:
        # Schedule the shutdown on the bot's event loop
        # This will cause the bot.run() call to eventually return
        future = asyncio.run_coroutine_threadsafe(_shutdown_bot(), bot_instance.loop)
        future.result(timeout=10) # Wait a short while for the shutdown to initiate
        return jsonify({"status": "success", "message": "봇 종료가 시작되었습니다. 재시작을 위해서는 외부 프로세스 관리자가 필요합니다."})
    except asyncio.TimeoutError:
        return jsonify({"status": "error", "error": "봇 종료 시작 시간 초과."}), 500
    except Exception as e:
        # FIX 1: Use root_logger here
        logger_module.root_logger.error(f"봇 종료 시작 중 오류 발생: {e}", exc_info=True)
        return jsonify({"status": "error", "error": f"종료 시작 실패: {e}"}), 500

@api_app.route('/control/reload_cogs', methods=['POST'])
def api_reload_cogs():
    """
    API Endpoint: POST /control/reload_cogs
    로드된 모든 코그(확장)를 다시 로드합니다.
    봇이 discord.py의 확장 시스템을 사용한다고 가정합니다.
    """
    global bot_instance
    if not bot_instance or not bot_instance.is_ready():
        return jsonify({"status": "error", "error": "Bot is not ready or offline."}), 500

    async def _reload_all_cogs():
        reloaded_cogs = []
        failed_cogs = []
        # Create a copy of keys as the dictionary might change during iteration
        for extension in list(bot_instance.extensions.keys()):
            try:
                await bot_instance.reload_extension(extension)
                reloaded_cogs.append(extension)
                bot_instance.logger.info(f"코그 다시 로드됨: {extension}")
            except Exception as e:
                failed_cogs.append(f"{extension} ({e})")
                bot_instance.logger.error(f"코그 다시 로드 실패 {extension}: {e}", exc_info=True)

        if not failed_cogs:
            return {"status": "success", "message": f"성공적으로 {len(reloaded_cogs)}개의 코그를 다시 로드했습니다."}
        else:
            return {"status": "error", "error": f"성공적으로 {len(reloaded_cogs)}개의 코그를 다시 로드했지만, 다음에서 실패했습니다: {', '.join(failed_cogs)}"}

    try:
        future = asyncio.run_coroutine_threadsafe(_reload_all_cogs(), bot_instance.loop)
        result = future.result(timeout=30) # Give more time for reloads
        return jsonify(result)
    except asyncio.TimeoutError:
        return jsonify({"status": "error", "error": "코그 다시 로드 시간 초과."}), 500
    except Exception as e:
        bot_instance.logger.error(f"코그 다시 로드 예약 중 오류 발생: {e}", exc_info=True)
        return jsonify({"status": "error", "error": f"코그 다시 로드 예약 실패: {e}"}), 500

@api_app.route('/control/update_git', methods=['POST'])
def api_update_git():
    """
    API Endpoint: POST /control/update_git
    Git pull을 트리거합니다.
    경고: 웹 서버에서 직접 셸 명령을 실행하는 것은 보안 위험이 될 수 있습니다.
    전용 배포 시스템이나 더 안전한 방법을 고려하십시오.
    """
    global bot_instance
    bot_directory = "/home/hws/Exceed/" # 봇의 저장소에 대한 올바른 경로인지 확인하십시오.

    try:
        # 'git pull' 명령을 봇 디렉토리에서 실행합니다.
        result = subprocess.run(
            ['git', 'pull'],
            cwd=bot_directory,
            capture_output=True,
            text=True,
            check=True # 0이 아닌 종료 코드에 대해 예외를 발생시킵니다.
        )
        bot_instance.logger.info(f"Git pull 출력:\n{result.stdout}")
        if "Already up to date." in result.stdout:
            message = "Git 저장소가 이미 최신 상태입니다."
        else:
            message = "Git pull 성공. 변경 사항을 적용하려면 봇 재시작이 필요할 수 있습니다."
        return jsonify({"status": "success", "message": message})
    except subprocess.CalledProcessError as e:
        bot_instance.logger.error(f"Git pull 실패: {e.stderr}", exc_info=True)
        return jsonify({"status": "error", "error": f"Git pull 실패: {e.stderr}"}), 500
    except FileNotFoundError:
        return jsonify({"status": "error", "error": "Git 명령을 찾을 수 없습니다. Git이 설치되어 있고 PATH에 있습니까?"}), 500
    except Exception as e:
        bot_instance.logger.error(f"Git 업데이트 중 예상치 못한 오류 발생: {e}", exc_info=True)
        return jsonify({"status": "error", "error": f"예상치 못한 오류 발생: {e}"}), 500

@api_app.route('/logs', methods=['GET'])
def get_logs():
    """
    API Endpoint: GET /logs
    봇의 최신 로그 파일 내용을 반환합니다.
    """
    log_file_path = logger_module.LOG_FILE_PATH # Use the path from logger.py
    try:
        # Read the last N lines to avoid reading huge files, or implement pagination
        with open(log_file_path, 'r', encoding='utf-8') as f:
            # For simplicity, reading all for now. Consider tailing or pagination for large logs.
            logs = f.readlines()
        return jsonify({"status": "success", "logs": logs})
    except FileNotFoundError:
        return jsonify({"status": "error", "error": "로그 파일을 찾을 수 없습니다."}), 404
    except Exception as e:
        global bot_instance
        if bot_instance:
            bot_instance.logger.error(f"로그 파일 읽기 실패: {e}", exc_info=True)
        return jsonify({"status": "error", "error": f"로그 파일 읽기 실패: {e}"}), 500

@api_app.route('/command_stats', methods=['GET'])
def get_command_stats():
    """
    API Endpoint: GET /command_stats
    가장 많이 사용된 명령어 통계를 반환합니다.
    """
    global bot_instance
    if not bot_instance or not bot_instance.pool:
        return jsonify({"status": "error", "error": "봇 또는 데이터베이스 풀을 사용할 수 없습니다."}), 500

    async def _fetch_command_stats():
        try:
            async with bot_instance.pool.acquire() as conn:
                rows = await conn.fetch("""
                    SELECT command_name, usage_count
                    FROM command_usage
                    ORDER BY usage_count DESC
                    LIMIT 10;
                """)
                return {"status": "success", "command_stats": [dict(row) for row in rows]}
        except Exception as e:
            bot_instance.logger.error(f"명령어 통계 가져오기 실패: {e}", exc_info=True)
            return {"status": "error", "error": f"명령어 통계 가져오기 실패: {e}"}

    try:
        # Use bot_instance.loop to run coroutine in bot's thread
        future = asyncio.run_coroutine_threadsafe(_fetch_command_stats(), bot_instance.loop)
        result = future.result(timeout=10)
        return jsonify(result)
    except asyncio.TimeoutError:
        return jsonify({"status": "error", "error": "명령어 통계 가져오기 시간 초과."}), 500
    except Exception as e:
        bot_instance.logger.error(f"명령어 통계 예약 중 오류 발생: {e}", exc_info=True)
        return jsonify({"status": "error", "error": f"명령어 통계 예약 실패: {e}"}), 500


def run_api_server():
    """
    Runs the Flask API server in a separate thread.
    """
    api_app.run(host='127.0.0.1', port=5001, debug=False) # Set debug=False in production!

# Global variable to hold the bot instance so Flask routes can access it
bot_instance = None

async def main():
    global bot_instance
    bot_instance = MyBot()

    # --- Logger Setup ---
    # This initializes the DiscordHandler and adds it to the root logger.
    if config.LOG_CHANNEL_ID is None:
        print("❌ WARNING: LOG_CHANNEL_ID is not set in config. Discord logging will not be enabled.")
    else:
        logger_module._configure_root_handlers(bot=bot_instance, discord_log_channel_id=bot_instance.log_channel_id)
        # FIX 2: Find the DiscordHandler instance and start its sending task immediately here.
        # This ensures the task is created on the bot's event loop and starts as soon as possible.
        discord_handler_instance = None
        for handler in logger_module.root_logger.handlers: # Iterate on root_logger.handlers
            if isinstance(handler, logger_module.DiscordHandler):
                discord_handler_instance = handler
                break

        if discord_handler_instance:
            bot_instance.loop.create_task(discord_handler_instance.start_sending_logs())
            logger_module.root_logger.info("Discord log sending task initiated during main setup.")
        else:
            logger_module.root_logger.error("❌ DiscordHandler not found after initial configuration. Logs won't go to Discord.")

    # Assign self.logger AFTER root handlers are configured and the task is started
    bot_instance.logger = logger_module.get_logger('기본 로그')
    # Use bot_instance.logger for the first time for a direct test
    bot_instance.logger.info("Bot instance logger initialized and ready.")

    # --- Database Pool Setup ---
    try:
        bot_instance.pool = await create_db_pool_in_bot() # CALL THE EMBEDDED FUNCTION
        bot_instance.logger.info("✅ 데이터베이스 연결 풀이 생성되었습니다.")
        await ensure_db_tables_exist(bot_instance.pool) # Ensure tables exist after pool creation
    except Exception as e:
        bot_instance.logger.critical(f"❌ 데이터베이스 연결 실패: {e}. 종료합니다.", exc_info=True)
        sys.exit(1)
    # --- End Database Pool Setup ---

    # --- Crash Log Handling ---
    # This section looks generally good.
    # The key is that after os.rename, you re-configure handlers, which means
    # you'll get a *new* DiscordHandler instance. Its task needs to be re-started.
    # The existing logic should handle this.
    log_file_path = pathlib.Path(__file__).parent / "logs" / "log.log" # Correct path
    if log_file_path.exists() and log_file_path.stat().st_size > 0:
        bot_instance.logger.info("이전 세션에서 'log.log' 파일이 발견되었습니다 (충돌 또는 비정상 종료 가능성).")

        for handler in logging.getLogger().handlers: # Using logging.getLogger() gets root_logger
            if hasattr(handler, 'flush'):
                handler.flush()

        crash_log_filename = f"log.log.CRASH-{datetime.datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}"
        crash_log_path = log_file_path.parent / crash_log_filename # Full path to the renamed crash log

        bot_instance.logger.info("로그 핸들러를 플러시하여 이름 변경 전 모든 이전 데이터가 기록되도록 합니다...")
        try:
            os.rename(log_file_path, crash_log_path)
            bot_instance.logger.info(f"충돌 로그를 처리용으로 {crash_log_path} (으)로 이름 변경했습니다.")

            try:
                uploaded_file_id = upload_to_drive.upload_log_to_drive(str(crash_log_path))
                if uploaded_file_id:
                    bot_instance.logger.info(f"✅ 충돌 로그가 Google Drive에 업로드되었습니다. 파일 ID: {uploaded_file_id}")
                else:
                    bot_instance.logger.warning("⚠️ 충돌 로그를 Google Drive에 업로드하지 못했습니다 (upload_to_drive.py 로그에서 세부 정보 확인).")
            except Exception as upload_error:
                bot_instance.logger.error(f"❌ Google Drive 업로드 중 오류 발생: {upload_error}", exc_info=True)

            # Re-configure handlers AFTER renaming the log file
            # This creates a NEW DiscordHandler instance.
            logger_module._configure_root_handlers(bot=bot_instance, discord_log_channel_id=bot_instance.log_channel_id)
            # FIX 3: Re-start the DiscordHandler's task after re-configuring handlers
            discord_handler_instance_after_crash = None
            for handler in logger_module.root_logger.handlers:
                if isinstance(handler, logger_module.DiscordHandler):
                    discord_handler_instance_after_crash = handler
                    break
            if discord_handler_instance_after_crash:
                bot_instance.loop.create_task(discord_handler_instance_after_crash.start_sending_logs())
                bot_instance.logger.info("충돌 로그 이름 변경 후 로그 핸들러를 다시 초기화하고 Discord 송신을 다시 시작했습니다.")
            else:
                bot_instance.logger.error("❌ 충돌 로그 처리 후 DiscordHandler를 다시 찾을 수 없어 Discord 송신을 다시 시작할 수 없습니다.")


        except OSError as e:
            bot_instance.logger.error(f"충돌 로그 파일 '{log_file_path}' 이름 변경 오류: {e}", exc_info=True)
            # Ensure handlers are at least configured, even if rename failed
            logger_module._configure_root_handlers(bot=bot_instance, discord_log_channel_id=bot_instance.log_channel_id)
            # And try to start its task too
            discord_handler_instance_after_rename_fail = None
            for handler in logger_module.root_logger.handlers:
                if isinstance(handler, logger_module.DiscordHandler):
                    discord_handler_instance_after_rename_fail = handler
                    break
            if discord_handler_instance_after_rename_fail:
                bot_instance.loop.create_task(discord_handler_instance_after_rename_fail.start_sending_logs())

        except Exception as e:
            bot_instance.logger.error(f"충돌 로그 처리 중 예상치 못한 오류 발생: {e}", exc_info=True)
            # Ensure handlers are at least configured
            logger_module._configure_root_handlers(bot=bot_instance, discord_log_channel_id=bot_instance.log_channel_id)
            # And try to start its task too
            discord_handler_instance_after_error = None
            for handler in logger_module.root_logger.handlers:
                if isinstance(handler, logger_module.DiscordHandler):
                    discord_handler_instance_after_error = handler
                    break
            if discord_handler_instance_after_error:
                bot_instance.loop.create_task(discord_handler_instance_after_error.start_sending_logs())


        old_log_files_after_rename = sorted(list(log_file_path.parent.glob('log.log.CRASH-*')))

        if old_log_files_after_rename:
            bot_instance.logger.info(f"시작 시 처리할 {len(old_log_files_after_rename)}개의 이전 로그 파일이 발견되었습니다.")
            for old_log_file in old_log_files_after_rename:
                if os.path.exists(old_log_file):
                    try:
                        os.remove(old_log_file)
                        bot_instance.logger.info(f"🗑️ 로컬 충돌 로그 파일 삭제됨: {old_log_file.name}.")
                    except Exception as delete_e:
                        bot_instance.logger.error(f"로컬 충돌 로그 파일 {old_log_file.name} 삭제 오류: {delete_e}", exc_info=True)
                else:
                    bot_instance.logger.info(f"로컬 충돌 로그 파일 {old_log_file.name}은(는) 이미 제거되었습니다 (아마도 업로드됨).")
        else:
            bot_instance.logger.info("시작 시 이름 변경 확인 후 처리할 보류 중인 충돌 로그 파일이 없습니다.")
    # --- End Crash Log Handling ---

    TOKEN = config.DISCORD_TOKEN # Assuming DISCORD_BOT_TOKEN is in config.py now
    if not TOKEN:
        # FIX 1: Use root_logger here before bot_instance.logger is guaranteed safe
        logger_module.root_logger.critical("DISCORD_TOKEN이 config.py에 설정되지 않았습니다. 종료합니다.")
        sys.exit(1)

    try:
        await bot_instance.start(TOKEN)
    except discord.HTTPException as e:
        bot_instance.logger.critical(f"HTTP 예외: {e} - 봇 토큰이 올바르고 인텐트가 활성화되었는지 확인하세요.")
    except Exception as e:
        bot_instance.logger.critical(f"봇 런타임 중 처리되지 않은 오류 발생: {e}", exc_info=True)
    finally:
        await bot_instance.close()
        bot_instance.logger.info("봇이 종료되었습니다.")


if __name__ == "__main__":
    # Start the Flask API in a separate thread
    api_thread = Thread(target=run_api_server)
    api_thread.daemon = True # Allow main program to exit even if thread is running
    api_thread.start()
    print(f"Existing Bot API running on http://127.0.0.1:5001")

    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        if bot_instance:
            bot_instance.logger.info("봇이 수동으로 중단되었습니다 (Ctrl+C). 종료합니다.")
        else:
            logger_module.root_logger.info("봇이 수동으로 중단되었습니다 (Ctrl+C). 종료합니다.")
    except Exception as e:
        if bot_instance:
            # If bot_instance exists, its logger should be safe to use here at cleanup
            bot_instance.logger.critical(f"봇 런타임 외부에서 치명적인 오류 발생: {e}", exc_info=True)
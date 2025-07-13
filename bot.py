import sys
import os
import shutil
import glob
import discord
from discord.ext import commands, tasks
import asyncio
import asyncpg
from datetime import datetime, timedelta, time
import pytz
import logging

from utils import config
from utils.logger import get_logger
from cogs.interview import DecisionButtonView  # Assuming this exists and is needed
from utils.upload_to_drive import upload_log_to_drive

intents = discord.Intents.default()
intents.guilds = True
intents.members = True
intents.message_content = True  # Enable if you need to read message content


class ExceedBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix="!", intents=intents)
        self.logger = None
        self.pool = None  # Database pool will be set later

    async def upload_and_delete_log_async(self, log_path):
        """Run the blocking upload_log_to_drive in an executor"""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, upload_log_to_drive, log_path)

    def _flush_log_handlers(self):
        """Flushes all file handlers associated with the root logger."""
        root_logger = logging.getLogger()
        for handler in root_logger.handlers:
            if isinstance(handler, logging.FileHandler):
                try:
                    handler.flush()
                    self.logger.debug(f"Flushed file handler: {handler.baseFilename}")
                except Exception as e:
                    self.logger.error(f"Error flushing log handler {handler.baseFilename}: {e}")

    async def on_disconnect(self):
        """Called when the bot loses connection to Discord (e.g., during restart or network issue)."""
        self.logger.info("Bot disconnected from Discord. Attempting graceful log handling.")
        await self._handle_log_on_shutdown()

    async def on_resumed(self):
        """Called when the bot successfully reconnects/resumes its Discord session."""
        self.logger.info("Bot reconnected/resumed Discord session.")

    async def _handle_log_on_shutdown(self):
        """
        Handles flushing and renaming the log file immediately before graceful shutdown.
        This ensures logs from the current session are captured for later upload.
        This is called on graceful exits (systemctl stop/restart, disconnect).
        """
        log_path = os.path.join("logs", "log.log")
        # Check if the file exists and is not empty (contains content from the current run)
        if os.path.exists(log_path) and os.path.getsize(log_path) > 0:
            self.logger.info("Flushing log handlers before graceful shutdown log capture...")
            self._flush_log_handlers()
            # Give a small moment for flush to complete (optional, but can help)
            await asyncio.sleep(0.1)

            timestamped_path = os.path.join("logs", f"log.log.{datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}")
            try:
                # Rename the log.log from THIS session
                shutil.move(log_path, timestamped_path)
                self.logger.info(f"Renamed current log to {timestamped_path} on graceful shutdown.")
                # We won't upload immediately here, as the process is about to die.
                # The next startup will handle uploading this specific file.
            except Exception as e:
                self.logger.error(f"❌ Failed to rename log on graceful shutdown: {e}")
        else:
            self.logger.info("No current log.log file (or it was empty) to rename on graceful shutdown.")

    async def setup_hook(self):
        """
        Called once the bot is ready to start connecting to Discord and setting up.
        This is where startup tasks like database connection, cog loading, and log processing occur.
        """
        # Initialize database pool with statement_cache_size=0
        try:
            self.pool = await asyncpg.create_pool(
                dsn=config.DATABASE_URL,
                statement_cache_size=0
            )
            self.logger.info("✅ Database pool created successfully.")
        except Exception as e:
            self.logger.critical(f"❌ Failed to create database pool: {e}", exc_info=True)
            # Exit if database connection fails, as bot won't function without it
            sys.exit(1)

        # Load cogs
        for filename in os.listdir("./cogs"):
            if filename.endswith(".py"):
                try:
                    await self.load_extension(f"cogs.{filename[:-3]}")
                    self.logger.info(f"코그 로드 완료: {filename}")
                except Exception as e:
                    self.logger.error(f"{filename} 로드 실패: {e}")

        # Sync slash commands globally or per guild as you want
        try:
            await self.tree.sync()
            self.logger.info("슬래시 명령어 동기화 완료.")
        except Exception as e:
            self.logger.error(f"❌ Failed to sync slash commands: {e}", exc_info=True)

        # --- IMPORTANT for Crash Recovery: Check for un-renamed log.log from previous run ---
        # If the bot crashed, log.log would NOT have been renamed by _handle_log_on_shutdown.
        # This ensures that log.log from a crash is still captured.
        current_session_log_path = os.path.join("logs", "log.log")
        if os.path.exists(current_session_log_path) and os.path.getsize(current_session_log_path) > 0:
            self.logger.info("Found a log.log file from previous session (likely a crash or abrupt exit).")
            self.logger.info("Flushing log handlers to ensure all prior data is written before renaming...")
            # Flush existing handlers one last time before moving the potentially old log.log
            self._flush_log_handlers()
            await asyncio.sleep(0.1)  # Give it a moment to write

            crash_timestamped_path = os.path.join("logs",
                                                  f"log.log.CRASH-{datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}")
            try:
                shutil.move(current_session_log_path, crash_timestamped_path)
                self.logger.info(f"Renamed crashed log to {crash_timestamped_path} for upload.")
            except Exception as e:
                self.logger.error(f"❌ Failed to rename crash log {current_session_log_path}: {e}")
        else:
            self.logger.info("No un-renamed log.log file found from previous session.")

        # Process any existing timestamped log files (including those from graceful shutdowns and crashes)
        old_log_files = glob.glob(os.path.join("logs", "log.log.*"))
        if old_log_files:
            self.logger.info(f"Found {len(old_log_files)} old log files to process on startup.")
            for old_log_path in old_log_files:
                self.logger.info(f"Uploading pending log file: {old_log_path}")
                await self.upload_and_delete_log_async(old_log_path)
        else:
            self.logger.info("No pending old log files to upload at startup.")

        # Start daily log upload task
        self.daily_log_upload_task.start()

    async def on_ready(self):
        """Called when the bot has successfully connected to Discord."""
        self.logger.info(f"{self.user} (ID: {self.user.id}) 로 로그인 성공")

        try:
            # Add persistent views here if they are not already managed by cogs
            self.add_view(DecisionButtonView())
            self.logger.info("Persistent view 등록 완료")
        except Exception as e:
            self.logger.error(f"Persistent view 등록 실패: {e}")

    @tasks.loop(count=1)
    async def daily_log_upload_task(self):
        """Runs once on startup to start the forever loop for daily log uploads."""
        self.logger.info("Starting daily log upload scheduler...")
        self.loop.create_task(self._daily_log_upload_forever())

    async def _daily_log_upload_forever(self):
        """
        The main loop for daily log uploads.
        Waits until midnight EST, then processes the current log.log.
        """
        est = pytz.timezone("US/Eastern")
        while True:
            now = datetime.now(est)
            # Calculate time until next midnight EST
            next_midnight = datetime.combine(now.date() + timedelta(days=1), time(0, 0, 0), tzinfo=est)
            seconds_until_midnight = (next_midnight - now).total_seconds()

            self.logger.info(f"Waiting {seconds_until_midnight:.2f}s until next log upload at midnight EST.")
            await asyncio.sleep(seconds_until_midnight)

            # Process current log.log for daily upload
            log_path = os.path.join("logs", "log.log")
            if os.path.exists(log_path) and os.path.getsize(log_path) > 0:
                self.logger.info("Flushing log handlers before daily upload.")
                self._flush_log_handlers()
                await asyncio.sleep(0.5)  # Give some time for flush to complete

                timestamped_path = os.path.join("logs", f"log.log.{datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}")
                try:
                    shutil.move(log_path, timestamped_path)
                    self.logger.info(f"Renamed current log to {timestamped_path} before daily upload.")
                    await self.upload_and_delete_log_async(timestamped_path)
                except Exception as e:
                    self.logger.error(f"❌ Failed to rename or upload daily log: {e}")
            else:
                self.logger.info("No current log.log file (or it was empty) to upload at daily task.")

            # Clean up old already-processed/uploaded timestamped logs (e.g., log.log.2025-07-12_...)
            # This is separate from the daily rotation performed by TimedRotatingFileHandler itself.
            old_logs_pattern = os.path.join("logs", "log.log.20*-*-*_*-*-*")  # Matches our timestamp format
            old_logs = glob.glob(old_logs_pattern)
            for old_log in old_logs:
                # Ensure we don't accidentally delete the currently running log.log
                # or a file that might have just been moved for upload in this same cycle.
                if os.path.basename(old_log) != "log.log":
                    try:
                        os.remove(old_log)
                        self.logger.info(f"Deleted old processed log file: {old_log}")
                    except Exception as e:
                        self.logger.error(f"Failed to delete old log file {old_log}: {e}")


def main():
    bot = ExceedBot()

    # Create root 'bot' logger ONCE, with Discord log channel integration
    bot.logger = get_logger(
        "기본 로그",  # This call will also configure the root logger's file handler
        bot=bot,
        discord_log_channel_id=config.LOG_CHANNEL_ID,
    )

    # --- NEW: Custom exception hook for crashes ---
    original_excepthook = sys.excepthook  # Store original for chaining if needed

    def custom_excepthook(exc_type, exc_value, exc_traceback):
        # Don't log KeyboardInterrupt as an error, let the finally block handle it
        if issubclass(exc_type, KeyboardInterrupt):
            original_excepthook(exc_type, exc_value, exc_traceback)
            return

        bot.logger.critical("An unhandled exception occurred, causing a crash:",
                            exc_info=(exc_type, exc_value, exc_traceback))
        # Attempt to flush logs one last time on crash
        # This relies on the 'buffering=1' in logger.py for real-time writing
        # but provides an explicit flush for any last-minute data.
        bot._flush_log_handlers()

        # Call original hook to exit properly, often prints to stderr
        original_excepthook(exc_type, exc_value, exc_traceback)

    sys.excepthook = custom_excepthook
    # --- END NEW ---

    # Fix Windows console code page to UTF-8, if on Windows
    if sys.platform == "win32":
        import ctypes
        ctypes.windll.kernel32.SetConsoleOutputCP(65001)

    try:
        bot.run(config.DISCORD_TOKEN)
    finally:
        # This block is executed when the bot.run() loop finishes,
        # whether due to KeyboardInterrupt, graceful shutdown, or certain types of errors.
        # It ensures that any remaining buffered logs are written to disk.
        bot.logger.info("Bot process ending. Ensuring log handlers are flushed.")
        bot._flush_log_handlers()


if __name__ == "__main__":
    main()
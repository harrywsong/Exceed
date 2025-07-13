# bot.py (Your main bot file - no changes needed from your last provided version)

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
import logging  # Import logging to access handlers

from utils import config
from utils.logger import get_logger
from cogs.interview import DecisionButtonView
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

    async def setup_hook(self):
        # Initialize database pool with statement_cache_size=0
        self.pool = await asyncpg.create_pool(
            dsn=config.DATABASE_URL,
            statement_cache_size=0
        )
        self.logger.info("✅ Database pool created successfully.")

        # Load cogs
        for filename in os.listdir("./cogs"):
            if filename.endswith(".py"):
                try:
                    await self.load_extension(f"cogs.{filename[:-3]}")
                    self.logger.info(f"코그 로드 완료: {filename}")
                except Exception as e:
                    self.logger.error(f"{filename} 로드 실패: {e}")

        # Sync slash commands globally or per guild as you want
        await self.tree.sync()
        self.logger.info("슬래시 명령어 동기화 완료.")

        # On startup: Upload current log.log if it exists (rename first)
        log_path = os.path.join("logs", "log.log")
        if os.path.exists(log_path):
            self.logger.info("Flushing log handlers before startup upload...")
            self._flush_log_handlers()

            await asyncio.sleep(0.5)

            timestamped_path = os.path.join("logs", f"log.log.{datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}")
            try:
                shutil.move(log_path, timestamped_path)
                self.logger.info(f"Renamed current log to {timestamped_path} before startup upload.")
                await self.upload_and_delete_log_async(timestamped_path)
            except Exception as e:
                self.logger.error(f"❌ Failed to rename or upload startup log: {e}")
        else:
            self.logger.info("No current log.log file to upload at startup.")

        # Start daily log upload task
        self.daily_log_upload_task.start()

    async def on_ready(self):
        self.logger.info(f"{self.user} (ID: {self.user.id}) 로 로그인 성공")

        try:
            self.add_view(DecisionButtonView())
            self.logger.info("Persistent view 등록 완료")
        except Exception as e:
            self.logger.error(f"Persistent view 등록 실패: {e}")

    @tasks.loop(count=1)
    async def daily_log_upload_task(self):
        """Runs once on startup to start the forever loop"""
        self.logger.info("Starting daily log upload scheduler...")
        self.loop.create_task(self._daily_log_upload_forever())

    async def _daily_log_upload_forever(self):
        est = pytz.timezone("US/Eastern")
        while True:
            now = datetime.now(est)
            next_midnight = datetime.combine(now.date() + timedelta(days=1), time(0, 0, 0), tzinfo=est)
            seconds_until_midnight = (next_midnight - now).total_seconds()

            self.logger.info(f"Waiting {seconds_until_midnight:.2f}s until next log upload at midnight EST.")
            await asyncio.sleep(seconds_until_midnight)

            # Delete old rotated logs first
            old_logs_pattern = os.path.join("logs", "log.log.*")
            old_logs = glob.glob(old_logs_pattern)
            for old_log in old_logs:
                try:
                    os.remove(old_log)
                    self.logger.info(f"Deleted old uploaded log file: {old_log}")
                except Exception as e:
                    self.logger.error(f"Failed to delete old log file {old_log}: {e}")

            # Rename current log.log to timestamped name before upload
            log_path = os.path.join("logs", "log.log")
            if os.path.exists(log_path):
                self.logger.info("Flushing log handlers before daily upload.")
                self._flush_log_handlers()

                await asyncio.sleep(0.5)

                timestamped_path = os.path.join("logs", f"log.log.{datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}")
                try:
                    shutil.move(log_path, timestamped_path)
                    self.logger.info(f"Renamed current log to {timestamped_path} before upload.")
                    await self.upload_and_delete_log_async(timestamped_path)
                except Exception as e:
                    self.logger.error(f"❌ Failed to rename or upload daily log: {e}")
            else:
                self.logger.info("No current log.log file to upload at daily task.")


def main():
    bot = ExceedBot()

    # Create root 'bot' logger ONCE, with Discord log channel integration
    bot.logger = get_logger(
        "기본 로그", # This call will now also configure the root logger's file handler
        bot=bot,
        discord_log_channel_id=config.LOG_CHANNEL_ID,
    )

    # Fix Windows console code page to UTF-8, if on Windows
    if sys.platform == "win32":
        import ctypes
        ctypes.windll.kernel32.SetConsoleOutputCP(65001)

    bot.run(config.DISCORD_TOKEN)


if __name__ == "__main__":
    main()
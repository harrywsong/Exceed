import sys
import os
import discord
from discord.ext import commands, tasks
import asyncio
import asyncpg
from datetime import datetime, timedelta, time
import pytz

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

        # Upload yesterday's log file on startup
        est = pytz.timezone("US/Eastern")
        yesterday = datetime.now(est) - timedelta(days=1)
        yesterday_log = os.path.join("logs", yesterday.strftime("%Y-%m-%d") + ".log")
        try:
            await self.upload_and_delete_log_async(yesterday_log)
        except Exception as e:
            self.logger.error(f"❌ 어제 로그 업로드 실패: {e}")

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
        # Just call the forever loop below
        self.logger.info("Starting daily log upload scheduler...")
        self.loop.create_task(self._daily_log_upload_forever())

    async def _daily_log_upload_forever(self):
        est = pytz.timezone("US/Eastern")
        while True:
            now = datetime.now(est)
            # Next midnight in EST timezone
            next_midnight = datetime.combine(now.date() + timedelta(days=1), time(0, 0, 0), tzinfo=est)
            seconds_until_midnight = (next_midnight - now).total_seconds()

            self.logger.info(f"Waiting {seconds_until_midnight:.2f}s until next log upload at midnight EST.")
            await asyncio.sleep(seconds_until_midnight)

            # Upload the previous day's log after midnight hits
            log_date = next_midnight.date() - timedelta(days=1)
            log_path = os.path.join("logs", f"{log_date.strftime('%Y-%m-%d')}.log")

            try:
                self.logger.info(f"Uploading daily log: {log_path}")
                await self.upload_and_delete_log_async(log_path)
            except Exception as e:
                self.logger.error(f"❌ 일일 로그 업로드 실패: {e}")

def main():
    bot = ExceedBot()

    # Create root 'bot' logger ONCE, with Discord log channel integration
    bot.logger = get_logger(
        "bot",
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

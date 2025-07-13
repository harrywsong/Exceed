import sys
import os
import discord
from discord.ext import commands
import asyncio
import asyncpg
from datetime import datetime, timedelta, time
import pytz
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
import pathlib

from utils import config
from utils.logger import get_logger
from cogs.interview import DecisionButtonView
from upload_to_drive import upload_log_to_drive

intents = discord.Intents.default()
intents.guilds = True
intents.members = True
intents.message_content = True

BASE_DIR = pathlib.Path(__file__).parent.resolve()
LOG_DIR = BASE_DIR / "logs"
LOG_DIR.mkdir(exist_ok=True)

class ExceedBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix="!", intents=intents)
        self.logger = None
        self.pool = None
        self.scheduler = AsyncIOScheduler(timezone="US/Eastern")

    async def setup_hook(self):
        self.pool = await asyncpg.create_pool(
            dsn=config.DATABASE_URL,
            statement_cache_size=0
        )
        self.logger.info("✅ Database pool created successfully.")

        for filename in os.listdir(BASE_DIR / "cogs"):
            if filename.endswith(".py"):
                try:
                    await self.load_extension(f"cogs.{filename[:-3]}")
                    self.logger.info(f"코그 로드 완료: {filename}")
                except Exception as e:
                    self.logger.error(f"{filename} 로드 실패: {e}")

        await self.tree.sync()
        self.logger.info("슬래시 명령어 동기화 완료.")

    async def on_ready(self):
        self.logger.info(f"{self.user} (ID: {self.user.id}) 로 로그인 성공")

        try:
            self.add_view(DecisionButtonView())
            self.logger.info("Persistent view 등록 완료")
        except Exception as e:
            self.logger.error(f"Persistent view 등록 실패: {e}")

        # Set presence example (optional)
        activity = discord.Activity(type=discord.ActivityType.watching, name="you sleep")
        await self.change_presence(activity=activity)

        # Immediately upload previous log on startup
        await self.upload_and_cleanup_log()

        # Schedule daily upload at 12 AM ET
        self.scheduler.add_job(
            self.upload_and_cleanup_log,
            CronTrigger(hour=0, minute=0, timezone="US/Eastern"),
            id="daily_log_upload",
            replace_existing=True
        )
        self.scheduler.start()

    async def upload_and_cleanup_log(self):
        """
        Upload the current log file to Google Drive, then delete and recreate a fresh empty log file.
        """
        eastern = pytz.timezone("US/Eastern")
        now_et = datetime.now(eastern)
        if now_et.time() < time(1, 0):  # before 1 AM ET, upload yesterday's log
            log_date = now_et.date() - timedelta(days=1)
        else:
            log_date = now_et.date()

        log_file_path = LOG_DIR / f"{log_date.strftime('%Y-%m-%d')}.log"
        log_file_path_str = str(log_file_path)

        if not os.path.exists(log_file_path_str):
            self.logger.warning(f"로그 파일이 없습니다: {log_file_path_str}, 업로드 생략")
            return

        self.logger.info(f"📤 Uploading log file {log_file_path_str} to Google Drive...")

        try:
            # Run synchronous upload function without blocking event loop
            await self.loop.run_in_executor(None, upload_log_to_drive, log_file_path_str)
            self.logger.info("✅ 업로드 성공!")

            # Delete the uploaded log file
            os.remove(log_file_path_str)
            self.logger.info(f"🗑️ 로그 파일 삭제 완료: {log_file_path_str}")

            # Recreate an empty file so logger can continue logging without error
            with open(log_file_path_str, "w", encoding="utf-8") as f:
                pass

            self.logger.info("🆕 새 로그 파일 생성 완료")

        except Exception as e:
            self.logger.error(f"❌ 업로드 또는 삭제 중 오류 발생: {e}")

def main():
    bot = ExceedBot()

    bot.logger = get_logger(
        "bot",
        bot=bot,
        discord_log_channel_id=config.LOG_CHANNEL_ID
    )

    if sys.platform == "win32":
        import ctypes
        ctypes.windll.kernel32.SetConsoleOutputCP(65001)

    bot.run(config.DISCORD_TOKEN)

if __name__ == "__main__":
    main()

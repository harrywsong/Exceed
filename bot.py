import sys
import os
import discord
from discord.ext import commands
import logging
import asyncpg
from datetime import datetime, timedelta, time
import pytz
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
import pathlib
import asyncio

from utils import config
from utils.logger import get_logger, DiscordLogHandler, FlushFileHandler
from cogs.interview import DecisionButtonView
from upload_to_drive import upload_log_to_drive

intents = discord.Intents.default()
intents.guilds = True
intents.members = True
intents.message_content = True

BASE_DIR = pathlib.Path(__file__).parent.resolve()
LOG_DIR = BASE_DIR / "logs" # <--- Remember to update this if you move your logs folder
LOG_DIR.mkdir(exist_ok=True)

class ExceedBot(commands.Bot):
    def __init__(self, logger):
        super().__init__(command_prefix="!", intents=intents)
        self.logger = logger
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
        # Discord log handler will be added later, after initial setup.

        self.logger.info(f"{self.user} (ID: {self.user.id}) 로 로그인 성공")

        try:
            self.add_view(DecisionButtonView())
            self.logger.info("Persistent view 등록 완료")
        except Exception as e:
            self.logger.error(f"Persistent view 등록 실패: {e}")

        activity = discord.Activity(type=discord.ActivityType.watching, name="you sleep")
        await self.change_presence(activity=activity)

        # Immediately upload previous log on startup
        await self.upload_and_cleanup_log(on_startup=True)

        # Allow time for initial cog messages to be sent and for Discord API to cool down.
        # This delay is crucial for preventing rate limits during the initial burst of messages.
        self.logger.info("초기화 완료 후 Discord API 쿨다운을 위해 잠시 대기 중...")
        await asyncio.sleep(3) # Increased to 3 seconds for a more robust cooldown

        # Now that initial burst of activities is mostly done, add the Discord log handler.
        # Log messages will now be sent to Discord.
        discord_handler = DiscordLogHandler(self, config.LOG_CHANNEL_ID)
        formatter = logging.Formatter(
            "[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s",
            "%Y-%m-%d %H:%M:%S"
        )
        discord_handler.setFormatter(formatter)
        self.logger.addHandler(discord_handler)
        self.logger.info("✅ Discord 로그 핸들러 추가 완료.") # This message will now go to Discord

        # Schedule daily upload at 12 AM ET
        self.scheduler.add_job(
            self.upload_and_cleanup_log,
            CronTrigger(hour=0, minute=0, timezone="US/Eastern"),
            id="daily_log_upload",
            replace_existing=True
        )
        self.scheduler.start()

    async def close_file_handlers(self):
        # Close and remove all file handlers to release file lock
        handlers_to_remove = []
        for handler in self.logger.handlers:
            if isinstance(handler, logging.FileHandler):
                handler.close()
                handlers_to_remove.append(handler)
        for handler in handlers_to_remove:
            self.logger.removeHandler(handler)
        self.logger.info("파일 핸들러 닫힘 및 제거 완료.")

    async def reinitialize_file_handler(self):
        # Re-add file handler (FlushFileHandler) to logger so logging continues
        formatter = logging.Formatter(
            "[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s",
            "%Y-%m-%d %H:%M:%S"
        )
        # Determine current log file path
        date_str = datetime.now().strftime("%Y-%m-%d")
        file_path = LOG_DIR / f"{date_str}.log"
        file_handler = FlushFileHandler(str(file_path), encoding='utf-8')
        file_handler.setFormatter(formatter)
        self.logger.addHandler(file_handler)
        self.logger.info("새 로그 파일 핸들러 생성 및 추가 완료.")


    async def upload_and_cleanup_log(self, on_startup=False):
        eastern = pytz.timezone("US/Eastern")
        now_et = datetime.now(eastern)

        # Determine which log file to process
        if on_startup:
            if now_et.time() < time(1, 0):
                log_date = now_et.date() - timedelta(days=1)
            else:
                log_date = now_et.date()
        else:
            log_date = now_et.date() - timedelta(days=1)


        log_file_to_process_path = LOG_DIR / f"{log_date.strftime('%Y-%m-%d')}.log"
        log_file_to_process_path_str = str(log_file_to_process_path)

        if not os.path.exists(log_file_to_process_path_str):
            self.logger.warning(f"처리할 로그 파일이 없습니다: {log_file_to_process_path_str}, 업로드 생략")
            return

        self.logger.info(f"📤 Google Drive에 로그 파일 {log_file_to_process_path_str} 업로드 중...")

        try:
            # Step 1: Close all file handlers to release the lock on the target log file.
            await self.close_file_handlers()
            self.logger.info(f"'{log_file_to_process_path_str}' 파일의 락을 해제하기 위해 파일 핸들러를 닫았습니다.")

            # Step 2: Run synchronous upload function without blocking event loop.
            await self.loop.run_in_executor(None, upload_log_to_drive, log_file_to_process_path_str)
            self.logger.info("✅ 업로드 성공!")

            # Step 3: Add a more significant delay to allow external processes to release the file handle
            await asyncio.sleep(1) # Wait for 1 second

            # Step 4: Attempt to rename the file first, then delete the renamed file.
            temp_log_file_path = LOG_DIR / f"temp_{log_date.strftime('%Y-%m-%d')}.log"
            temp_log_file_path_str = str(temp_log_file_path)

            try:
                os.rename(log_file_to_process_path_str, temp_log_file_path_str)
                self.logger.info(f"🔄 로그 파일 '{log_file_to_process_path_str}'을(를) '{temp_log_file_path_str}'(으)로 이름 변경 완료.")
                await asyncio.sleep(0.05) # Small sleep after rename, if rename succeeds
                os.remove(temp_log_file_path_str)
                self.logger.info(f"🗑️ 임시 로그 파일 삭제 완료: {temp_log_file_path_str}")

            except OSError as e: # Catch OSError specifically for file operations
                self.logger.error(f"❌ 로그 파일 이름 변경 또는 삭제 중 오류 발생 (WinError 32 예상): {e}. 원본 파일 유지: {log_file_to_process_path_str}")
                # If rename/delete fails, the old log file might still exist as the original name.
                # The next step will ensure a new log file is created for current logging.

            # Step 5: Recreate the empty log file for the current day.
            current_log_file_path = LOG_DIR / f"{datetime.now().strftime('%Y-%m-%d')}.log"
            with open(current_log_file_path, "w", encoding="utf-8") as f:
                pass
            self.logger.info(f"🆕 새 로그 파일 '{current_log_file_path}' 생성 완료")

            # Step 6: Re-add the file handler to the logger to resume file logging for the current day's log.
            await self.reinitialize_file_handler()

        except Exception as e:
            self.logger.error(f"❌ 업로드 중 예상치 못한 오류 발생: {e}")
            # If an unexpected error occurs, ensure file handlers are re-initialized so logging can continue.
            await self.reinitialize_file_handler()

def main():
    # Initialize logger with console + file only.
    logger = get_logger("bot")

    if sys.platform == "win32":
        import ctypes
        ctypes.windll.kernel32.SetConsoleOutputCP(65001)

    bot = ExceedBot(logger)
    bot.run(config.DISCORD_TOKEN)

if __name__ == "__main__":
    main()
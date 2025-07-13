import sys
import os
import discord
from discord.ext import commands
import asyncio
import asyncpg  # Make sure asyncpg is installed: pip install asyncpg
from datetime import datetime

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

    async def setup_hook(self):
        # Initialize database pool here with statement_cache_size=0
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

    async def on_ready(self):
        self.logger.info(f"{self.user} (ID: {self.user.id}) 로 로그인 성공")

        try:
            self.add_view(DecisionButtonView())
            self.logger.info("Persistent view 등록 완료")
        except Exception as e:
            self.logger.error(f"Persistent view 등록 실패: {e}")

        # Upload today's log file asynchronously without blocking
        log_file = os.path.join("logs", datetime.now().strftime("%Y-%m-%d") + ".log")
        loop = asyncio.get_event_loop()
        loop.run_in_executor(None, upload_log_to_drive, log_file)

def main():
    bot = ExceedBot()

    bot.logger = get_logger(
        "bot",
        bot=bot,
        discord_log_channel_id=config.LOG_CHANNEL_ID
    )

    # Fix Windows console code page to UTF-8, if on Windows
    if sys.platform == "win32":
        import ctypes
        ctypes.windll.kernel32.SetConsoleOutputCP(65001)

    bot.run(config.DISCORD_TOKEN)

if __name__ == "__main__":
    main()

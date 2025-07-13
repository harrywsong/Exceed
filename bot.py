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
import asyncpg # <--- ADDED: Import for PostgreSQL async operations

import utils.config as config
import utils.logger as logger_module
from utils import upload_to_drive

# --- Database Functions (Moved from utils/database.py) ---
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

# close_db_pool is handled directly in MyBot.close() now

# --- Discord Bot Setup ---
class MyBot(commands.Bot):
    def __init__(self):
        super().__init__(
            command_prefix=commands.when_mentioned_or("!"),
            intents=discord.Intents.all(),
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

    async def setup_hook(self):
        for ext in self.initial_extensions:
            try:
                await self.load_extension(ext)
                logger_module.root_logger.info(f"코그 로드 완료: {ext.split('.')[-1]}.py")
            except Exception as e:
                log_func = self.logger.error if hasattr(self, 'logger') else logger_module.root_logger.error
                log_func(f"확장 로드 실패 {ext}: {e}", exc_info=True)

        try:
            await self.tree.sync()
            self.logger.info("슬래시 명령어 동기화 완료.")
        except Exception as e:
            self.logger.error(f"슬래시 명령어 동기화 실패: {e}", exc_info=True)

    async def on_ready(self):
        self.ready_event.set()
        self.logger.info(f"{self.user} (ID: {self.user.id}) 로 로그인 성공")

    async def close(self):
        if self.pool: # Ensure pool is closed when bot closes
            await self.pool.close()
            self.logger.info("✅ 데이터베이스 풀이 닫혔습니다.")
        await self.session.close()
        for handler in logging.getLogger().handlers:
            if hasattr(handler, 'flush'):
                handler.flush()
            if hasattr(handler, 'close'):
                handler.close()
        await super().close()
        self.logger.info("봇이 종료되었습니다.")

    async def on_command_error(self, ctx, error):
        if isinstance(error, commands.CommandNotFound):
            return
        self.logger.error(f"명령어 '{ctx.command}' 실행 중 오류 발생: {error}", exc_info=True)
        await ctx.send(f"오류가 발생했습니다: {error}")


async def main():
    bot = MyBot()

    # --- Logger Setup ---
    logger_module._configure_root_handlers(bot=bot, discord_log_channel_id=bot.log_channel_id)
    bot.logger = logger_module.get_logger('기본 로그')

    # --- Database Pool Setup ---
    try:
        bot.pool = await create_db_pool_in_bot() # CALL THE EMBEDDED FUNCTION
        bot.logger.info("✅ 데이터베이스 연결 풀이 생성되었습니다.")
    except Exception as e:
        bot.logger.critical(f"❌ 데이터베이스 연결 실패: {e}. 종료합니다.", exc_info=True)
        sys.exit(1)
    # --- End Database Pool Setup ---

    # --- Crash Log Handling ---
    log_file_path = pathlib.Path(__file__).parent / "logs" / "log.log"
    if log_file_path.exists() and log_file_path.stat().st_size > 0:
        bot.logger.info("이전 세션에서 'log.log' 파일이 발견되었습니다 (충돌 또는 비정상 종료 가능성).")

        for handler in logging.getLogger().handlers:
            if hasattr(handler, 'flush'):
                handler.flush()

        crash_log_filename = f"log.log.CRASH-{datetime.datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}"
        crash_log_path = log_file_path.parent / crash_log_filename # Full path to the renamed crash log

        bot.logger.info("로그 핸들러를 플러시하여 이름 변경 전 모든 이전 데이터가 기록되도록 합니다...")
        try:
            os.rename(log_file_path, crash_log_path)
            bot.logger.info(f"충돌 로그를 처리용으로 {crash_log_path} (으)로 이름 변경했습니다.")

            try:
                uploaded_file_id = upload_to_drive.upload_log_to_drive(str(crash_log_path))
                if uploaded_file_id:
                    bot.logger.info(f"✅ 충돌 로그가 Google Drive에 업로드되었습니다. 파일 ID: {uploaded_file_id}")
                else:
                    bot.logger.warning("⚠️ 충돌 로그를 Google Drive에 업로드하지 못했습니다 (upload_to_drive.py 로그에서 세부 정보 확인).")
            except Exception as upload_error:
                bot.logger.error(f"❌ Google Drive 업로드 중 오류 발생: {upload_error}", exc_info=True)

            logger_module._configure_root_handlers(bot=bot, discord_log_channel_id=bot.log_channel_id)
            bot.logger.info("충돌 로그 이름 변경 후 로그 핸들러를 다시 초기화했습니다.")

        except OSError as e:
            bot.logger.error(f"충돌 로그 파일 '{log_file_path}' 이름 변경 오류: {e}", exc_info=True)
            logger_module._configure_root_handlers(bot=bot, discord_log_channel_id=bot.log_channel_id)
        except Exception as e:
            bot.logger.error(f"충돌 로그 처리 중 예상치 못한 오류 발생: {e}", exc_info=True)
            logger_module._configure_root_handlers(bot=bot, discord_log_channel_id=bot.log_channel_id)

        old_log_files_after_rename = sorted(list(log_file_path.parent.glob('log.log.CRASH-*')))

        if old_log_files_after_rename:
            bot.logger.info(f"시작 시 처리할 {len(old_log_files_after_rename)}개의 이전 로그 파일이 발견되었습니다.")
            for old_log_file in old_log_files_after_rename:
                if os.path.exists(old_log_file):
                    try:
                        os.remove(old_log_file)
                        bot.logger.info(f"🗑️ 로컬 충돌 로그 파일 삭제됨: {old_log_file.name}.")
                    except Exception as delete_e:
                        bot.logger.error(f"로컬 충돌 로그 파일 {old_log_file.name} 삭제 오류: {delete_e}", exc_info=True)
                else:
                    bot.logger.info(f"로컬 충돌 로그 파일 {old_log_file.name}은(는) 이미 제거되었습니다 (아마도 업로드됨).")
        else:
            bot.logger.info("시작 시 이름 변경 확인 후 처리할 보류 중인 충돌 로그 파일이 없습니다.")
    # --- End Crash Log Handling ---

    TOKEN = config.DISCORD_TOKEN # Assuming DISCORD_BOT_TOKEN is in config.py now
    if not TOKEN:
        bot.logger.critical("DISCORD_TOKEN이 config.py에 설정되지 않았습니다. 종료합니다.")
        sys.exit(1)

    try:
        await bot.start(TOKEN)
    except discord.HTTPException as e:
        bot.logger.critical(f"HTTP 예외: {e} - 봇 토큰이 올바르고 인텐트가 활성화되었는지 확인하세요.")
    except Exception as e:
        bot.logger.critical(f"봇 런타임 중 처리되지 않은 오류 발생: {e}", exc_info=True)
    finally:
        await bot.close()
        bot.logger.info("봇이 중지되었습니다.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger_module.root_logger.info("봇이 수동으로 중단되었습니다 (Ctrl+C). 종료합니다.")
    except Exception as e:
        logger_module.root_logger.critical(f"봇 런타임 외부에서 치명적인 오류 발생: {e}", exc_info=True)
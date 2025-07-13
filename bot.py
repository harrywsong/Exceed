# /home/hws/Exceed/bot.py (Modified to remove Firebase)

import discord
from discord.ext import commands, tasks
import os
import asyncio
import datetime
import aiohttp
# REMOVE THESE FIREBASE IMPORTS
# import firebase_admin
# from firebase_admin import credentials, storage
import json  # Keep if used elsewhere
import logging
import re
import random
import string
import pytz
import traceback
import sys
import pathlib

# Import your custom logger module
import utils.logger as logger_module  # Renamed to avoid conflict with `logging`


# REMOVE THIS LINE (unless you have another use for it)
# SERVICE_ACCOUNT_KEY_PATH = 'firebase_credentials.json'

# --- REMOVE THIS ENTIRE FIREBASE INITIALIZATION BLOCK ---
# def initialize_firebase():
#     if not firebase_admin._apps: # Check if Firebase is already initialized
#         try:
#             cred = credentials.Certificate(SERVICE_ACCOUNT_KEY_PATH)
#             firebase_admin.initialize_app(cred, {'storageBucket': 'exceed-bot.appspot.com'})
#         except Exception as e:
#             sys.exit(1)
# initialize_firebase()
# --- END REMOVE ---

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
        self.log_channel_id = 1389739434110484612  # Your actual log channel ID

    async def setup_hook(self):
        for ext in self.initial_extensions:
            try:
                await self.load_extension(ext)
                logger_module.root_logger.info(f"코그 로드 완료: {ext.split('.')[-1]}.py")
            except Exception as e:
                logger_module.root_logger.error(f"Failed to load extension {ext}: {e}", exc_info=True)

        try:
            await self.tree.sync()
            self.logger.info("슬래시 명령어 동기화 완료.")
        except Exception as e:
            self.logger.error(f"Failed to sync slash commands: {e}", exc_info=True)

        self.add_view(HelpView(self))
        self.add_view(CloseTicketView(self))
        self.logger.info("Persistent view 등록 완료")

    async def on_ready(self):
        self.ready_event.set()
        self.logger.info(f"{self.user} (ID: {self.user.id}) 로 로그인 성공")

        ticket_cog = self.get_cog('TicketSystem')
        if ticket_cog:
            # REMOVE or COMMENT OUT this line:
            # await ticket_cog.setup_persistent_views()
            self.logger.info(
                "[티켓 시스템] Persistent views (HelpView, CloseTicketView) 등록 완료.")  # This log is also redundant if it's already in the cog
            # This log will now correctly appear from the cog itself, so this line can also be removed if it's a duplicate.

        interview_cog = self.get_cog('InterviewRequestCog')
        if interview_cog:
            await interview_cog.post_interview_message()
            self.logger.info("[클랜 인터뷰] 인터뷰 요청 메시지 및 영구 뷰 설정 완료.")

        self.start_log_upload_scheduler()

    @tasks.loop(hours=24)
    async def daily_log_upload(self):
        # This task will run daily, but without Firebase, it won't upload.
        # You can remove this @tasks.loop and the method if not needed.
        await self.upload_logs()  # This will now be an empty function if Firebase is removed.

    def start_log_upload_scheduler(self):
        if not self.daily_log_upload.is_running():
            now = datetime.datetime.now(pytz.timezone('America/New_York'))
            next_midnight = (now + datetime.timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
            wait_seconds = (next_midnight - now).total_seconds()

            self.logger.info(f"Starting daily log upload scheduler...")
            self.logger.info(f"Waiting {wait_seconds:.2f}s until next log upload at midnight EST.")

            self.loop.call_later(wait_seconds, self.daily_log_upload.start)

    # --- REMOVE THIS ENTIRE upload_logs METHOD ---
    async def upload_logs(self):
        # bucket = storage.bucket() # This line will cause an error
        log_dir = pathlib.Path(__file__).parent / "logs"
        uploaded_count = 0

        old_log_files = sorted(list(log_dir.glob('log.log.CRASH-*')))

        if not old_log_files:
            self.logger.info("No old crash log files to upload.")
            return

        for old_log_file in old_log_files:
            try:
                # All this logic depends on Firebase and should be removed if not using it.
                self.logger.info(f"Skipping upload of {old_log_file.name} as Firebase is not configured.")

                # If you still want to delete local crash logs without uploading, uncomment this:
                # os.remove(old_log_file)
                # self.logger.info(f"🗑️ Deleted local log file: {old_log_file.name}")

            except Exception as e:
                self.logger.error(f"Error handling log file {old_log_file.name}: {e}", exc_info=True)

        # This line will always be 0 if upload logic is removed.
        # if uploaded_count > 0:
        #     self.logger.info(f"Successfully uploaded {uploaded_count} log files.")

    # --- END REMOVE ---

    async def close(self):
        await self.session.close()
        for handler in logging.getLogger().handlers:
            if hasattr(handler, 'flush'):
                handler.flush()
            if hasattr(handler, 'close'):
                handler.close()
        await super().close()

    async def on_command_error(self, ctx, error):
        if isinstance(error, commands.CommandNotFound):
            return
        self.logger.error(f"Error in command {ctx.command}: {error}", exc_info=True)
        await ctx.send(f"An error occurred: {error}")


class HelpView(discord.ui.View):
    def __init__(self, bot):
        super().__init__(timeout=None)
        self.bot = bot

    pass


class CloseTicketView(discord.ui.View):
    def __init__(self, bot):
        super().__init__(timeout=None)
        self.bot = bot

    pass


async def main():
    bot = MyBot()
    bot.logger = logger_module.get_logger('기본 로그', bot=bot, discord_log_channel_id=bot.log_channel_id)

    log_file_path = pathlib.Path(__file__).parent / "logs" / "log.log"
    if log_file_path.exists() and log_file_path.stat().st_size > 0:
        bot.logger.info("Found a log.log file from previous session (likely a crash or abrupt exit).")

        for handler in logging.getLogger().handlers:
            if hasattr(handler, 'flush'):
                handler.flush()

        crash_log_filename = f"log.log.CRASH-{datetime.datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}"
        crash_log_path = log_file_path.parent / crash_log_filename

        bot.logger.info("Flushing log handlers to ensure all prior data is written before renaming...")
        try:
            os.rename(log_file_path, crash_log_path)
            bot.logger.info(f"Renamed crashed log to {crash_log_path} for processing.")

            logger_module._configure_root_handlers()
            bot.logger.info("Re-initialized log handlers after crash log rename.")

        except OSError as e:
            bot.logger.error(f"Error renaming crashed log file '{log_file_path}': {e}")
            logger_module._configure_root_handlers()
        except Exception as e:
            bot.logger.error(f"An unexpected error occurred during crash log handling: {e}", exc_info=True)
            logger_module._configure_root_handlers()

            # --- IMPORTANT: Modify this section as well ---
        # If you are not uploading to Firebase, you need to decide what to do with these crash logs.
        # Option 1: Just delete them.
        # Option 2: Move them to an 'archive' folder if you want to keep them locally.
        # Option 3: Implement a different upload mechanism (e.g., to S3, a different cloud storage, or even Discord)

        old_log_files_after_rename = sorted(list(log_file_path.parent.glob('log.log.CRASH-*')))

        if old_log_files_after_rename:
            bot.logger.info(f"Found {len(old_log_files_after_rename)} old log files to process on startup.")
            for old_log_file in old_log_files_after_rename:
                # If you remove Firebase, this `try...except` block needs to change.
                # Here, we'll just delete them.
                try:
                    os.remove(old_log_file)
                    bot.logger.info(f"🗑️ Deleted local crash log file: {old_log_file.name} (no upload configured).")
                except Exception as delete_e:
                    bot.logger.error(f"Error deleting local crash log file {old_log_file.name}: {delete_e}",
                                     exc_info=True)
        else:
            bot.logger.info("No pending crash log file found for immediate processing after startup rename check.")

    # If you are not uploading logs, you can remove the daily_log_upload task as well.
    # Otherwise, it will just run a scheduled task that does nothing.
    # bot.logger.info("Starting daily log upload scheduler...")
    # bot.start_log_upload_scheduler()

    TOKEN = os.getenv("DISCORD_BOT_TOKEN")
    if not TOKEN:
        bot.logger.critical("DISCORD_BOT_TOKEN environment variable not set. Exiting.")
        sys.exit(1)

    try:
        await bot.start(TOKEN)
    except discord.HTTPException as e:
        bot.logger.critical(f"HTTP Exception: {e} - Ensure your bot token is correct and has intents enabled.")
    except Exception as e:
        bot.logger.critical(f"An unhandled error occurred during bot runtime: {e}", exc_info=True)
    finally:
        await bot.close()
        bot.logger.info("Bot has stopped.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger_module.root_logger.info("Bot manually interrupted (Ctrl+C). Shutting down.")
    except Exception as e:
        logger_module.root_logger.critical(f"Fatal error outside bot runtime: {e}", exc_info=True)
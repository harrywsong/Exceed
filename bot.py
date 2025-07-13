import discord
from discord.ext import commands, tasks
import os
import asyncio
import datetime
import aiohttp
import firebase_admin
from firebase_admin import credentials, storage
import json
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

# Ensure this matches the correct path to your service account key
SERVICE_ACCOUNT_KEY_PATH = 'firebase_credentials.json'


# --- Firebase Initialization ---
def initialize_firebase():
    if not firebase_admin._apps:  # Check if Firebase is already initialized
        try:
            cred = credentials.Certificate(SERVICE_ACCOUNT_KEY_PATH)
            firebase_admin.initialize_app(cred, {'storageBucket': 'exceed-bot.appspot.com'})
            # logger_module.root_logger.info("Firebase Admin SDK initialized successfully.") # Use root logger here
        except Exception as e:
            # logger_module.root_logger.error(f"Failed to initialize Firebase Admin SDK: {e}") # Use root logger here
            sys.exit(1)  # Exit if Firebase fails to initialize


initialize_firebase()


# --- Discord Bot Setup ---
class MyBot(commands.Bot):
    def __init__(self):
        super().__init__(
            command_prefix=commands.when_mentioned_or("!"),
            intents=discord.Intents.all(),
            sync_commands_debug=True  # Keep this for debugging, remove in production
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
        self.ready_event = asyncio.Event()  # For voice cog cleanup
        self.log_channel_id = 123456789012345678  # Replace with your actual log channel ID

    async def setup_hook(self):
        # Load extensions (cogs)
        for ext in self.initial_extensions:
            try:
                await self.load_extension(ext)
                # UNCOMMENTED: Log successful cog loading to root logger
                logger_module.root_logger.info(f"코그 로드 완료: {ext.split('.')[-1]}.py")
            except Exception as e:
                # UNCOMMENTED: Log errors during cog loading to root logger
                logger_module.root_logger.error(f"Failed to load extension {ext}: {e}", exc_info=True)

        # Sync slash commands globally (or to specific guild during development)
        try:
            # For development, specify guild_ids=...
            # await self.tree.sync(guild=discord.Object(id=YOUR_GUILD_ID))
            await self.tree.sync()
            self.logger.info("슬래시 명령어 동기화 완료.")
        except Exception as e:
            self.logger.error(f"Failed to sync slash commands: {e}", exc_info=True)

        self.add_view(HelpView(self))  # Register persistent views
        self.add_view(CloseTicketView(self))  # Register persistent views

        self.logger.info("Persistent view 등록 완료")
    async def on_ready(self):
        # logger_module.root_logger.info(f"{self.user} (ID: {self.user.id}) 로 로그인 성공") # Use root logger
        self.ready_event.set()  # Signal that bot is ready for voice cleanup
        self.logger.info(f"{self.user} (ID: {self.user.id}) 로 로그인 성공")

        # Restart persistent views for Ticket System if necessary
        ticket_cog = self.get_cog('TicketSystem')
        if ticket_cog:
            await ticket_cog.setup_persistent_views()
            self.logger.info("[티켓 시스템] Persistent views (HelpView, CloseTicketView) 등록 완료.")

        # Ensure clan interview message is posted and persistent view is set up
        interview_cog = self.get_cog('InterviewRequestCog')
        if interview_cog:
            await interview_cog.post_interview_message()
            self.logger.info("[클랜 인터뷰] 인터뷰 요청 메시지 및 영구 뷰 설정 완료.")

        # Start log upload scheduler
        self.start_log_upload_scheduler()

    @tasks.loop(hours=24)
    async def daily_log_upload(self):
        # This task will run daily to upload logs.
        # It's okay if it tries to upload a non-existent log.log, it will just skip.
        await self.upload_logs()

    def start_log_upload_scheduler(self):
        if not self.daily_log_upload.is_running():
            now = datetime.datetime.now(pytz.timezone('America/New_York'))  # EST timezone
            # Calculate time until next midnight EST
            next_midnight = (now + datetime.timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
            wait_seconds = (next_midnight - now).total_seconds()

            self.logger.info(f"Starting daily log upload scheduler...")
            self.logger.info(f"Waiting {wait_seconds:.2f}s until next log upload at midnight EST.")

            self.loop.call_later(wait_seconds, self.daily_log_upload.start)
            # You might want to call daily_log_upload.before_loop(self.wait_until_ready) if not already handled

    async def upload_logs(self):
        bucket = storage.bucket()
        log_dir = pathlib.Path(__file__).parent / "logs"
        uploaded_count = 0

        # Process all files starting with 'log.log.CRASH-'
        # This function should only be called by the scheduler or after a crash detection
        old_log_files = sorted(list(log_dir.glob('log.log.CRASH-*')))

        if not old_log_files:
            self.logger.info("No old crash log files to upload.")
            return

        for old_log_file in old_log_files:
            try:
                # Determine the filename for Google Drive (e.g., 2025-07-13_01-13-32.log)
                # Extract timestamp from 'log.log.CRASH-YYYY-MM-DD_HH-MM-SS'
                timestamp_match = re.search(r'CRASH-(\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2})', old_log_file.name)
                if timestamp_match:
                    drive_filename = f"{timestamp_match.group(1)}.log"
                else:
                    drive_filename = f"manual_upload_{datetime.datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}.log"

                blob = bucket.blob(drive_filename)
                blob.upload_from_filename(str(old_log_file))

                # Make the file publicly accessible for view (optional, consider security)
                blob.make_public()

                self.logger.info(f"✅ Uploaded {old_log_file.name} to Google Drive as {drive_filename}")
                self.logger.info(f"🔗 File link: {blob.public_url}")

                os.remove(old_log_file)
                self.logger.info(f"🗑️ Deleted local log file: {old_log_file.name}")
                uploaded_count += 1

            except Exception as e:
                self.logger.error(f"Error uploading or deleting log file {old_log_file.name}: {e}", exc_info=True)

        if uploaded_count > 0:
            self.logger.info(f"Successfully uploaded {uploaded_count} log files.")

    async def close(self):
        await self.session.close()
        # Flush all handlers one last time before closing the bot
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


# --- Helper classes for persistent views (e.g., Ticket System) ---
class HelpView(discord.ui.View):
    # Your HelpView implementation
    def __init__(self, bot):
        super().__init__(timeout=None)
        self.bot = bot

    # Define your buttons and callbacks here
    # Example:
    # @discord.ui.button(label="Help", style=discord.ButtonStyle.primary, custom_id="persistent_help")
    # async def help_button_callback(self, interaction: discord.Interaction, button: discord.ui.Button):
    #     await interaction.response.send_message("This is the help message!", ephemeral=True)
    pass


class CloseTicketView(discord.ui.View):
    # Your CloseTicketView implementation
    def __init__(self, bot):
        super().__init__(timeout=None)
        self.bot = bot

    # Define your buttons and callbacks here
    # Example:
    # @discord.ui.button(label="Close Ticket", style=discord.ButtonStyle.danger, custom_id="persistent_close_ticket")
    # async def close_ticket_button_callback(self, interaction: discord.Interaction, button: discord.ui.Button):
    #     await interaction.response.send_message("Ticket closed!", ephemeral=True)
    pass


# --- Main execution block ---
async def main():
    bot = MyBot()
    # Initialize the main logger for the bot
    # It will automatically use the root_logger configured in logger_module
    bot.logger = logger_module.get_logger('기본 로그', bot=bot, discord_log_channel_id=bot.log_channel_id)

    # --- IMPORTANT: Your existing crash log handling logic ---
    log_file_path = pathlib.Path(__file__).parent / "logs" / "log.log"
    if log_file_path.exists() and log_file_path.stat().st_size > 0:
        bot.logger.info("Found a log.log file from previous session (likely a crash or abrupt exit).")

        # Flush all current handlers before renaming the file
        # This tries to ensure all buffered data is written before the file is moved.
        for handler in logging.getLogger().handlers:
            if hasattr(handler, 'flush'):
                handler.flush()

        crash_log_filename = f"log.log.CRASH-{datetime.datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}"
        crash_log_path = log_file_path.parent / crash_log_filename

        bot.logger.info("Flushing log handlers to ensure all prior data is written before renaming...")
        try:
            os.rename(log_file_path, crash_log_path)
            bot.logger.info(f"Renamed crashed log to {crash_log_path} for upload.")

            # --- CRITICAL NEW STEP HERE ---
            # After renaming the log file, re-configure the root logger handlers.
            # This forces the TimedRotatingFileHandler to close its old handle and open a new one
            # to the correct, now empty, log.log file.
            logger_module._configure_root_handlers()
            bot.logger.info("Re-initialized log handlers after crash log rename.")

        except OSError as e:
            bot.logger.error(f"Error renaming crashed log file '{log_file_path}': {e}")
            # If renaming fails, we might still have an old file handle.
            # Attempt to reconfigure handlers anyway, though the old file might persist.
            logger_module._configure_root_handlers()
        except Exception as e:
            bot.logger.error(f"An unexpected error occurred during crash log handling: {e}", exc_info=True)
            logger_module._configure_root_handlers()

            # Now handle the upload of the just-renamed crash log
        old_log_files_after_rename = sorted(list(log_file_path.parent.glob('log.log.CRASH-*')))

        if old_log_files_after_rename:
            bot.logger.info(f"Found {len(old_log_files_after_rename)} old log files to process on startup.")
            for old_log_file in old_log_files_after_rename:
                if old_log_file == crash_log_path:  # Only upload the one we just renamed
                    try:
                        bucket = storage.bucket()
                        timestamp_match = re.search(r'CRASH-(\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2})', old_log_file.name)
                        if timestamp_match:
                            drive_filename = f"{timestamp_match.group(1)}.log"
                        else:
                            drive_filename = f"manual_upload_{datetime.datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}.log"

                        blob = bucket.blob(drive_filename)
                        blob.upload_from_filename(str(old_log_file))
                        blob.make_public()  # Make public for easier sharing/viewing

                        bot.logger.info(f"✅ Uploaded {old_log_file.name} to Google Drive as {drive_filename}")
                        bot.logger.info(f"🔗 File link: {blob.public_url}")

                        os.remove(old_log_file)
                        bot.logger.info(f"🗑️ Deleted local log file: {old_log_file.name}")
                    except Exception as upload_e:
                        bot.logger.error(f"Error during upload/deletion of {old_log_file.name}: {upload_e}",
                                         exc_info=True)
        else:
            bot.logger.info("No pending crash log file found for immediate upload after startup rename check.")

    # Start daily log upload scheduler after initial setup
    bot.logger.info("Starting daily log upload scheduler...")
    bot.start_log_upload_scheduler()  # This will schedule the first daily upload.

    # Retrieve your bot token from environment variables or a config file
    # For security, avoid hardcoding tokens directly in the script.
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
    # Wrap the main coroutine in asyncio.run()
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger_module.root_logger.info("Bot manually interrupted (Ctrl+C). Shutting down.")
    except Exception as e:
        logger_module.root_logger.critical(f"Fatal error outside bot runtime: {e}", exc_info=True)
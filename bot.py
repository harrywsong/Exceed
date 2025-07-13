# /home/hws/Exceed/bot.py (relevant section in main function)

import discord
from discord.ext import commands, tasks
import os
import asyncio
import datetime
import aiohttp
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
import utils.logger as logger_module # Keep this import

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
        self.log_channel_id = 1389739434110484612 # Your Discord log channel ID

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

    async def on_ready(self):
        self.ready_event.set()
        self.logger.info(f"{self.user} (ID: {self.user.id}) 로 로그인 성공")

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

async def main():
    bot = MyBot()

    # --- THIS IS THE CRUCIAL PART ---
    # Call _configure_root_handlers to set up all loggers, including DiscordHandler,
    # passing the bot instance and channel ID. This needs to happen BEFORE any cogs are loaded
    # or the bot tries to log to Discord.
    logger_module._configure_root_handlers(bot=bot, discord_log_channel_id=bot.log_channel_id)
    bot.logger = logger_module.get_logger('기본 로그') # Get the named logger AFTER root handlers are set

    log_file_path = pathlib.Path(__file__).parent / "logs" / "log.log"
    if log_file_path.exists() and log_file_path.stat().st_size > 0:
        bot.logger.info("Found a log.log file from previous session (likely a crash or abrupt exit).")

        # The flushing logic here is good for ensuring data is written before renaming.
        # However, _configure_root_handlers now handles closing/removing existing handlers,
        # so explicit `handler.flush()` for all might be redundant if _configure_root_handlers is robust.
        # But for crash recovery, it's safer to leave this explicit flush.
        for handler in logging.getLogger().handlers:
            if hasattr(handler, 'flush'):
                handler.flush()

        crash_log_filename = f"log.log.CRASH-{datetime.datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}"
        crash_log_path = log_file_path.parent / crash_log_filename

        bot.logger.info("Flushing log handlers to ensure all prior data is written before renaming...")
        try:
            os.rename(log_file_path, crash_log_path)
            bot.logger.info(f"Renamed crashed log to {crash_log_path} for processing.")

            # --- Re-configure handlers *after* renaming the old log file ---
            # IMPORTANT: Pass bot and discord_log_channel_id again here!
            logger_module._configure_root_handlers(bot=bot, discord_log_channel_id=bot.log_channel_id)
            bot.logger.info("Re-initialized log handlers after crash log rename.")

        except OSError as e:
            bot.logger.error(f"Error renaming crashed log file '{log_file_path}': {e}")
            logger_module._configure_root_handlers(bot=bot, discord_log_channel_id=bot.log_channel_id) # Also here!
        except Exception as e:
            bot.logger.error(f"An unexpected error occurred during crash log handling: {e}", exc_info=True)
            logger_module._configure_root_handlers(bot=bot, discord_log_channel_id=bot.log_channel_id) # And here!

        old_log_files_after_rename = sorted(list(log_file_path.parent.glob('log.log.CRASH-*')))

        if old_log_files_after_rename:
            bot.logger.info(f"Found {len(old_log_files_after_rename)} old log files to process on startup.")
            for old_log_file in old_log_files_after_rename:
                try:
                    os.remove(old_log_file)
                    bot.logger.info(f"🗑️ Deleted local crash log file: {old_log_file.name} (no upload configured).")
                except Exception as delete_e:
                    bot.logger.error(f"Error deleting local crash log file {old_log_file.name}: {delete_e}",
                                     exc_info=True)
        else:
            bot.logger.info("No pending crash log file found for immediate processing after startup rename check.")

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
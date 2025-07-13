# /home/hws/Exceed/bot.py

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
import utils.logger as logger_module


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
        self.log_channel_id = 1389739434110484612

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

        # Ensure these views are imported if they are not already.
        # Assuming HelpView and CloseTicketView are defined below or imported from elsewhere.
        # If they are in a cog that is loaded, the cog's on_ready will add them.
        # If they are only defined globally in bot.py, they need to be added here for persistence.
        # Based on your previous logs, these were handled by the TicketSystem cog's on_ready.
        # If you removed those lines from the cog, you might need to add them here.
        # However, your provided ticket.py already handles them in its on_ready.
        # So, the following lines might be redundant if the cog correctly adds them.
        # For safety, if you confirmed the cog adds them, these could be removed.
        # If they're generic views not tied to a specific cog, keep them here.

        # Checking your ticket.py, it appears to add HelpView and CloseTicketView
        # within its own on_ready. If that's the case, these lines below are
        # likely redundant and can be removed to avoid duplicate registrations.
        # For now, I'll keep them as they don't cause harm if views are unique.
        # But consider removing if the cog truly handles their full persistent setup.
        # This setup_hook handles views that are *not* tied to a specific cog's lifecycle.

        # If HelpView and CloseTicketView are meant to be tied to the TicketSystem cog,
        # they should ideally be added as persistent views within the TicketSystem cog's
        # own `on_ready` or `setup_hook` method.
        # Based on your `ticket.py` this is already happening:
        # `self.bot.add_view(HelpView(self.bot, self.logger))`
        # `self.bot.add_view(CloseTicketView(self.bot, self.logger))`
        # Therefore, these lines below in bot.py's setup_hook are redundant for these specific views
        # and can be removed to rely solely on the cog's setup for its views.

        # For simplicity and to avoid confusion, I'm going to assume HelpView and CloseTicketView
        # are indeed handled by the `TicketSystem` cog and remove these lines from `bot.py`'s `setup_hook`.
        # If you have other generic persistent views that don't belong to any cog, they would go here.
        # self.add_view(HelpView(self))
        # self.add_view(CloseTicketView(self))
        # self.logger.info("Persistent view 등록 완료") # This log is now handled by the cog.

    async def on_ready(self):
        self.ready_event.set()
        self.logger.info(f"{self.user} (ID: {self.user.id}) 로 로그인 성공")

        # The setup of persistent views for TicketSystem and InterviewRequestCog
        # happens within their respective cogs' on_ready methods.
        # Therefore, these explicit calls in bot.py are no longer needed
        # and were causing AttributeError due to method name mismatches.

        # ticket_cog = self.get_cog('TicketSystem')
        # if ticket_cog:
        #     # The log below will now correctly appear from the cog itself,
        #     # so this line can also be removed if it's a duplicate.
        #     self.logger.info("[티켓 시스템] Persistent views (HelpView, CloseTicketView) 등록 완료.")

        # interview_cog = self.get_cog('InterviewRequestCog')
        # if interview_cog:
        #    # This method 'post_interview_message' does not exist in InterviewRequestCog.
        #    # The correct method for posting the message is 'send_interview_request_message'
        #    # and it is already called within InterviewRequestCog's own on_ready.
        #    # So, this line is removed.
        #    # await interview_cog.post_interview_message()
        #    self.logger.info("[클랜 인터뷰] 인터뷰 요청 메시지 및 영구 뷰 설정 완료.") # This log is also redundant

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


# --- Views (If not part of a cog and needed globally) ---
# If these views are only used by the TicketSystem cog and are added by the cog,
# you don't need them defined globally here.
# Assuming they might be used elsewhere or need global registration if not handled by cog.
# Based on your ticket.py, these classes are imported and passed to the cog,
# and the cog handles `bot.add_view`. So, these global definitions are likely not needed
# unless you intend to add them directly from bot.py's setup_hook as well (which would be redundant).
# For now, I'll keep them as placeholders, but be aware they might be removable if fully cog-managed.

# from views.help_view import HelpView # assuming path 'views/help_view.py'
# from views.ticket_views import CloseTicketView # assuming path 'views/ticket_views.py'

# If HelpView and CloseTicketView are *only* defined in a separate file like views/ticket_views.py
# and are handled by the TicketSystem cog's on_ready, then their class definitions
# do *not* need to be here. I'm removing the placeholder `pass` definitions.
# Ensure they are properly imported in relevant files if they're not global.


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
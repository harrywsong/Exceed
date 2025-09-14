# /home/hws/Exceed/utils/logger.py

import logging
import sys
import pathlib
import asyncio
import threading
from logging.handlers import TimedRotatingFileHandler
import discord  # Ensure discord is imported

LOG_FILE_PATH = pathlib.Path(__file__).parent.parent / "logs" / "log.log"
LOG_FILE_PATH.parent.mkdir(parents=True, exist_ok=True)

LOGGING_FORMATTER = logging.Formatter(
    "[{asctime}] [{levelname:.<8}] [{name}] {message}",
    datefmt="%Y-%m-%d %H:%M:%S",
    style="{",
)
CONSOLE_FORMATTER = logging.Formatter(
    "[{asctime}] [{levelname:.<8}] [{name}] {message}",
    datefmt="%Y-%m-%d %H:%M:%S",
    style="{",
)

root_logger = logging.getLogger()
root_logger.setLevel(logging.INFO)


class DiscordHandler(logging.Handler):
    """
    A custom logging handler to send log messages to a Discord channel,
    buffering messages until the bot is ready.
    """

    def __init__(self, bot, channel_id):
        super().__init__()
        self.bot = bot
        self.channel_id = channel_id
        self._message_buffer = []
        self._send_task = None  # Task is initially None, started when bot is ready
        self._buffer_lock = threading.Lock()  # Use threading.Lock for synchronous access
        self.stopped = False  # Flag to indicate if the handler is closing

    def emit(self, record):
        """
        Emit a log record. This method is called synchronously by the logging system,
        so we need to handle the buffering without async operations.
        """
        log_entry = self.format(record)
        if self.stopped:  # Don't buffer if closing
            return

        # Use thread-safe buffer access since emit() is called synchronously
        with self._buffer_lock:
            self._message_buffer.append(log_entry)

        # Try to ensure the send task is running, but don't create coroutines here
        self._ensure_send_task()

    def _ensure_send_task(self):
        """
        Ensures the send task is running if we have an event loop and the bot is ready.
        This is called from emit() but doesn't create async operations.
        """
        try:
            # Check if there's a running event loop
            loop = asyncio.get_running_loop()
            if loop and (self._send_task is None or self._send_task.done()):
                # Only schedule if we don't already have a pending task
                if not hasattr(self, '_task_scheduled'):
                    self._task_scheduled = True
                    # Use call_soon_threadsafe since emit() might be called from another thread
                    loop.call_soon_threadsafe(self._schedule_send_task)
        except RuntimeError:
            # No running event loop - this is fine, the task will be started when bot is ready
            pass

    def _schedule_send_task(self):
        """
        Schedules the send task from within the event loop.
        """
        if self._send_task is None or self._send_task.done():
            self._send_task = asyncio.create_task(self._send_buffered_logs())
        self._task_scheduled = False

    async def _send_buffered_logs(self):
        """
        Periodically sends buffered logs to Discord.
        This task must only be started AFTER the bot is ready.
        """
        # Ensure the bot is ready before doing anything Discord-related
        try:
            await self.bot.wait_until_ready()
        except RuntimeError:
            print("DiscordHandler: Bot not ready, _send_buffered_logs cannot proceed.", file=sys.stderr)
            return

        print("DiscordHandler: Bot is ready, starting to send buffered logs.")  # Debug print

        while not self.stopped:
            try:
                await asyncio.sleep(5)  # Adjust sending interval as needed

                # Use thread-safe buffer access
                messages_to_send = []
                with self._buffer_lock:
                    if self._message_buffer:
                        messages_to_send = self._message_buffer[:]
                        self._message_buffer.clear()

                if not messages_to_send:
                    continue  # Nothing to send

                channel = self.bot.get_channel(self.channel_id)
                if not channel:
                    print(
                        f"❌ Discord log channel {self.channel_id} not found. Clearing {len(messages_to_send)} buffered logs.",
                        file=sys.stderr)
                    continue

                for msg_content in messages_to_send:
                    try:
                        # Chunk messages to fit Discord's limit
                        for chunk in self._chunk_message(msg_content, 1900):
                            await channel.send(f"```\n{chunk}\n```")
                            await asyncio.sleep(0.7)  # Delay to respect Discord's rate limits
                    except discord.Forbidden:
                        print(
                            f"❌ DiscordHandler: Missing permissions to send messages to log channel {self.channel_id}.",
                            file=sys.stderr)
                        break  # Stop trying to send if permissions are an issue
                    except discord.HTTPException as e:
                        print(f"❌ Discord HTTP error sending log chunk: {e}", file=sys.stderr)
                    except Exception as e:
                        print(f"❌ Failed to send log to Discord channel: {e}", file=sys.stderr)
            except asyncio.CancelledError:
                print("DiscordHandler: _send_buffered_logs task cancelled.")
                break  # Exit the loop if cancelled
            except Exception as e:
                print(f"DiscordHandler: Unexpected error in send loop: {e}", file=sys.stderr)

    def _chunk_message(self, msg, max_length):
        """Splits a message into chunks that fit Discord's character limit."""
        lines = msg.splitlines(keepends=True)
        chunk = ""
        for line in lines:
            if len(chunk) + len(line) > max_length:
                if chunk:
                    yield chunk
                chunk = line
            else:
                chunk += line
        if chunk:
            yield chunk

    def start_sending_logs(self):
        """
        Starts the asynchronous task to send buffered logs to Discord.
        This should be called once the bot is ready.
        """
        if self._send_task is None or self._send_task.done():
            self._send_task = asyncio.create_task(self._send_buffered_logs())
            print("DiscordHandler: Log sending task created and started.")  # Debug print

    def close(self):
        self.stopped = True  # Signal the task to stop
        if self._send_task and not self._send_task.done():
            self._send_task.cancel()
        self._send_task = None
        super().close()


def _configure_root_handlers(bot=None, discord_log_channel_id=None):
    """
    Configures or re-configures the root logger's file, console, and Discord handlers.
    This function is crucial for re-establishing handlers after log file
    renaming operations (e.g., crash log upload) and for initial setup.
    """
    handlers_to_remove = []
    for handler in root_logger.handlers:
        handlers_to_remove.append(handler)

    for handler in handlers_to_remove:
        try:
            handler.close()
        except Exception as e:
            print(f"Error closing handler {type(handler).__name__}: {e}", file=sys.stderr)
        root_logger.removeHandler(handler)

    file_handler = TimedRotatingFileHandler(
        filename=str(LOG_FILE_PATH),
        when="midnight",
        interval=1,
        backupCount=30,
        encoding='utf-8',
        utc=False,
        delay=False,
    )
    file_handler.suffix = "%Y-%m-%d"
    file_handler.setFormatter(LOGGING_FORMATTER)
    root_logger.addHandler(file_handler)

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(CONSOLE_FORMATTER)
    root_logger.addHandler(console_handler)

    if bot and discord_log_channel_id:
        discord_handler = DiscordHandler(bot, discord_log_channel_id)
        # CHANGED: Set DiscordHandler level to INFO to capture more logs
        discord_handler.setLevel(logging.INFO)
        discord_handler.setFormatter(LOGGING_FORMATTER)
        root_logger.addHandler(discord_handler)
        # Start the log sending task for DiscordHandler
        # This is crucial to ensure the buffered logs are actually sent
        discord_handler.start_sending_logs()


# FIXED: get_logger now accepts **kwargs to catch unexpected arguments
def get_logger(name: str, level=logging.INFO, **kwargs) -> logging.Logger:
    """
    Retrieves a logger with the specified name and level.
    The DiscordHandler is now managed by the root logger configuration.
    Accepts **kwargs to allow for backward compatibility with old cog calls
    that might pass 'bot' or 'discord_log_channel_id'. These arguments are
    ignored by this function as handler configuration is done globally.
    """
    logger = logging.getLogger(name)
    logger.setLevel(level)
    logger.propagate = True  # Allow logs to propagate to root handlers (including DiscordHandler)
    return logger


logging.getLogger('discord').setLevel(logging.INFO)
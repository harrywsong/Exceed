# utils/logger.py

import logging
import sys
import pathlib
import asyncio
from logging.handlers import TimedRotatingFileHandler
import discord # Make sure discord is imported

LOG_FILE_PATH = pathlib.Path(__file__).parent.parent / "logs" / "log.log"
LOG_FILE_PATH.parent.mkdir(parents=True, exist_ok=True)

# Using consistent formatter for all for simplicity, can be customized
LOGGING_FORMATTER = logging.Formatter(
    "[{asctime}] [{levelname:.<8}] [{name}] {message}",
    datefmt="%Y-%m-%d %H:%M:%S",
    style="{",
)
CONSOLE_FORMATTER = logging.Formatter( # Kept separate for potential future customization
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
        self._send_task = None
        self._buffer_lock = asyncio.Lock()
        # Store a reference to the bot's ready_event for explicit waiting
        self._bot_ready_event = bot.ready_event # <-- Get the asyncio.Event from bot

        self.setLevel(logging.WARNING) # Set default level here

    def emit(self, record):
        log_entry = self.format(record)
        # Ensure that the coroutine is scheduled on the bot's event loop
        # This is crucial when emit is called from a different thread (e.g., Flask)
        try:
            if self.bot and self.bot.loop and self.bot.loop.is_running():
                asyncio.run_coroutine_threadsafe(self._add_to_buffer(log_entry), self.bot.loop)
            else:
                # If event loop isn't running yet (very early startup), just buffer
                # The _send_buffered_logs will eventually pick it up once loop starts
                # and bot is ready.
                self._message_buffer.append(log_entry)
        except Exception as e:
            # Fallback for very early errors where even buffering might be tricky
            print(f"ERROR: Could not schedule log to Discord buffer: {e} - Log: {log_entry}", file=sys.stderr)


    async def _add_to_buffer(self, msg):
        async with self._buffer_lock:
            self._message_buffer.append(msg)
            # Only create a new send task if one isn't already running
            if self._send_task is None or self._send_task.done():
                self._send_task = asyncio.create_task(self._send_buffered_logs())

    async def _send_buffered_logs(self):
        # Wait until the bot explicitly signals it is ready
        await self._bot_ready_event.wait() # <-- Use the bot's ready_event here

        async with self._buffer_lock:
            if not self._message_buffer:
                return

            channel = self.bot.get_channel(self.channel_id)
            if not channel:
                print(f"❌ Discord log channel {self.channel_id} not found. Clearing {len(self._message_buffer)} buffered logs.", file=sys.stderr)
                self._message_buffer.clear()
                return

            messages_to_send = self._message_buffer[:]
            self._message_buffer.clear()

            for msg_content in messages_to_send:
                try:
                    for chunk in self._chunk_message(msg_content, 1900):
                        await channel.send(f"```\n{chunk}\n```")
                        await asyncio.sleep(0.7) # Add a small delay to avoid rate limits
                except discord.Forbidden:
                    print(f"❌ Discord Forbidden: Bot lacks permissions to send in channel {self.channel_id}", file=sys.stderr)
                    # Don't re-buffer, as it's a persistent permission issue
                except discord.HTTPException as e:
                    print(f"❌ Discord HTTP error sending log chunk: {e} (Status: {e.status})", file=sys.stderr)
                    # Optionally re-buffer if it's a transient error, but for now, just print
                except Exception as e:
                    print(f"❌ Failed to send log to Discord channel: {e}", file=sys.stderr)
                    # Optionally re-buffer if it's a transient error, but for now, just print


    def _chunk_message(self, msg, max_length):
        """Splits a message into chunks that fit Discord's character limit."""
        lines = msg.splitlines(keepends=True)
        chunk = ""
        for line in lines:
            # Check if adding the current line exceeds the max_length for the current chunk
            # Also, ensure that if a single line is too long, it's chunked properly
            if len(chunk) + len(line) > max_length and chunk: # If chunk is not empty, yield it
                yield chunk
                chunk = "" # Reset chunk for the current line

            # If the current line itself is longer than max_length, chunk it
            while len(line) > max_length:
                yield line[:max_length]
                line = line[max_length:]

            chunk += line # Add remaining part of line (or whole line if it was short)

        if chunk: # Yield any remaining chunk
            yield chunk


def _configure_root_handlers(bot=None, discord_log_channel_id=None):
    """
    Configures or re-configures the root logger's file, console, and Discord handlers.
    This function is crucial for re-establishing handlers after log file
    renaming operations (e.g., crash log upload) and for initial setup.
    """
    # Close and remove existing handlers to prevent duplicates
    handlers_to_remove = root_logger.handlers[:]
    for handler in handlers_to_remove:
        try:
            handler.close()
        except Exception as e:
            print(f"Error closing handler {type(handler).__name__}: {e}", file=sys.stderr)
        root_logger.removeHandler(handler)

    # File handler
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

    # Console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(CONSOLE_FORMATTER)
    root_logger.addHandler(console_handler)

    # Discord handler - Only add if bot and channel ID are provided
    # The DiscordHandler itself will wait for bot.ready_event
    if bot and discord_log_channel_id:
        discord_handler = DiscordHandler(bot, discord_log_channel_id)
        discord_handler.setLevel(logging.DEBUG) # Adjust level as needed
        discord_handler.setFormatter(LOGGING_FORMATTER)
        root_logger.addHandler(discord_handler)

def get_logger(name: str, level=logging.INFO) -> logging.Logger:
    """
    Retrieves a logger with the specified name and level.
    The DiscordHandler is now managed by the root logger configuration.
    """
    logger = logging.getLogger(name)
    logger.setLevel(level)
    logger.propagate = True # Allow messages to pass up to the root logger's handlers
    return logger

# Set the logging level for discord.py's internal logger
logging.getLogger('discord').setLevel(logging.INFO)
logging.getLogger('discord.http').setLevel(logging.INFO) # Reduce spam from HTTP requests
logging.getLogger('websockets').setLevel(logging.INFO) # Reduce spam from websockets
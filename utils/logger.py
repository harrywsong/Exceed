import logging
import sys
import pathlib
import asyncio
from logging.handlers import TimedRotatingFileHandler
import discord  # Ensure discord is imported for HTTPException handling in DiscordHandler

# Define log file path
# This will create 'logs/log.log' in the parent directory of utils/logger.py
LOG_FILE_PATH = pathlib.Path(__file__).parent.parent / "logs" / "log.log"
LOG_FILE_PATH.parent.mkdir(parents=True, exist_ok=True)  # Ensure logs directory exists

# Define logging formatters
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

# Get the root logger
root_logger = logging.getLogger()
root_logger.setLevel(logging.INFO)  # Set overall minimum level for the root logger


# --- NEW/MODIFIED: DiscordHandler with buffering ---
class DiscordHandler(logging.Handler):
    """
    A custom logging handler to send log messages to a Discord channel,
    buffering messages until the bot is ready.
    """

    def __init__(self, bot, channel_id):
        super().__init__()
        self.bot = bot
        self.channel_id = channel_id
        self._message_buffer = []  # Buffer for messages before bot is ready
        self._send_task = None  # Task to send buffered messages
        self._buffer_lock = asyncio.Lock()  # Lock for buffer access

        # Set the level for this handler.
        # CHANGE THIS TO logging.INFO or logging.WARNING if you want more logs in Discord.
        self.setLevel(logging.WARNING) # Default to WARNING, adjust as needed

    def emit(self, record):
        # Format the record immediately
        log_entry = self.format(record)
        # Use asyncio.ensure_future to add to buffer, handling async from sync context
        # This is safe because it schedules the coroutine on the running event loop.
        # If no loop is running yet, it will be scheduled once the loop starts.
        try:
            asyncio.ensure_future(self._add_to_buffer(log_entry))
        except RuntimeError:
            # This can happen if emit is called before the event loop starts
            # or after it has closed. In such cases, log to stderr as a fallback.
            print(f"DEBUG: No running event loop for DiscordHandler. Buffering for later: {log_entry}", file=sys.stderr)
            self._message_buffer.append(log_entry) # Manually add to buffer if loop not ready

    async def _add_to_buffer(self, msg):
        async with self._buffer_lock:
            self._message_buffer.append(msg)
            # If a send task isn't running or is done, start a new one
            if self._send_task is None or self._send_task.done():
                self._send_task = asyncio.create_task(self._send_buffered_logs())

    async def _send_buffered_logs(self):
        # Wait until the bot is ready and connected
        await self.bot.wait_until_ready()

        async with self._buffer_lock:
            if not self._message_buffer:
                return

            channel = self.bot.get_channel(self.channel_id)
            if not channel:
                print(f"❌ Discord log channel {self.channel_id} not found. Clearing {len(self._message_buffer)} buffered logs.", file=sys.stderr)
                self._message_buffer.clear()
                return

            # Take all messages from the buffer
            messages_to_send = self._message_buffer[:]
            self._message_buffer.clear()

            for msg_content in messages_to_send:
                try:
                    # Discord message limit is 2000 characters.
                    # Split into chunks if necessary (though logs are usually shorter).
                    for chunk in self._chunk_message(msg_content, 1900): # 1900 to leave space for ```
                        await channel.send(f"```\n{chunk}\n```")
                        await asyncio.sleep(0.7) # Small delay to avoid rate limits
                except discord.HTTPException as e:
                    print(f"❌ Discord HTTP error sending log chunk: {e}", file=sys.stderr)
                except Exception as e:
                    print(f"❌ Failed to send log to Discord channel: {e}", file=sys.stderr)

    def _chunk_message(self, msg, max_length):
        """Splits a message into chunks that fit Discord's character limit."""
        lines = msg.splitlines(keepends=True)
        chunk = ""
        for line in lines:
            if len(chunk) + len(line) > max_length:
                yield chunk
                chunk = line
            else:
                chunk += line
        if chunk:
            yield chunk


# --- MODIFIED: _configure_root_handlers to accept bot and add DiscordHandler ---
def _configure_root_handlers(bot=None, discord_log_channel_id=None):
    """
    Configures or re-configures the root logger's file, console, and Discord handlers.
    This function is crucial for re-establishing handlers after log file
    renaming operations (e.g., crash log upload) and for initial setup.
    """
    # Close and remove existing handlers from root logger
    handlers_to_remove = []
    for handler in root_logger.handlers:
        handlers_to_remove.append(handler)

    for handler in handlers_to_remove:
        try:
            handler.close()  # Close the handler's stream to release resources
        except Exception as e:
            # Log to stderr if closing fails, as our logger might be gone
            print(f"Error closing handler {type(handler).__name__}: {e}", file=sys.stderr)
        root_logger.removeHandler(handler)

    # Add TimedRotatingFileHandler to the ROOT logger
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

    # Add a StreamHandler for console output (journalctl captures this)
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(CONSOLE_FORMATTER)
    root_logger.addHandler(console_handler)

    # --- NEW: Add DiscordHandler to root_logger if bot and channel ID are provided ---
    if bot and discord_log_channel_id:
        discord_handler = DiscordHandler(bot, discord_log_channel_id)
        # Set the level here. Default to WARNING. Change to INFO if you want more logs.
        discord_handler.setLevel(logging.WARNING) # You can change this to logging.INFO
        discord_handler.setFormatter(LOGGING_FORMATTER)
        root_logger.addHandler(discord_handler)


# --- MODIFIED: get_logger function ---
# This function no longer needs to add the DiscordHandler, as it's now added to the root.
def get_logger(name: str, level=logging.INFO, bot=None, discord_log_channel_id=None) -> logging.Logger:
    """
    Retrieves a logger with the specified name and level.
    The DiscordHandler is now managed by the root logger configuration.
    """
    logger = logging.getLogger(name)
    logger.setLevel(level)
    # Ensure named loggers propagate messages up to the root logger
    logger.propagate = True
    return logger

# --- IMPORTANT: This initial call is now removed from here, as it needs the bot instance.
# It will be called from bot.py's main function after bot initialization.
# _configure_root_handlers() # REMOVE THIS LINE from here


# Explicitly set discord.py's logger level (good practice)
logging.getLogger('discord').setLevel(logging.INFO)
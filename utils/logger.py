# utils/logger.py
import logging
import sys
import pathlib
import asyncio
from logging.handlers import TimedRotatingFileHandler

BASE_DIR = pathlib.Path(__file__).parent.parent.resolve()
LOG_DIR = BASE_DIR / "logs"
LOG_DIR.mkdir(exist_ok=True, parents=True)  # Ensure logs directory exists

# Define a global formatter for consistency
LOGGING_FORMATTER = logging.Formatter(
    "[%(asctime)s] [%(levelname)-7s] [%(name)s] %(message)s",
    "%Y-%m-%d %H:%M:%S"
)
LOG_FILE_PATH = LOG_DIR / "log.log"  # Use pathlib for path construction


class DiscordLogHandler(logging.Handler):
    def __init__(self, bot, channel_id):
        super().__init__()
        self.bot = bot
        self.channel_id = channel_id
        self._message_buffer = []
        self._send_task = None
        self._buffer_lock = asyncio.Lock()

    def emit(self, record):
        # Ensure bot is ready before attempting to send logs to Discord
        # Also, check for discord.HTTPException if discord is not imported globally
        if not self.bot or not self.bot.is_ready():
            return

        try:
            msg = self.format(record)
            asyncio.ensure_future(self._add_to_buffer(msg))
        except Exception:
            self.handleError(record)

    async def _add_to_buffer(self, msg):
        async with self._buffer_lock:
            self._message_buffer.append(msg)
            if self._send_task is None or self._send_task.done():
                self._send_task = asyncio.create_task(self.send_buffered_logs())

    async def send_buffered_logs(self):
        async with self._buffer_lock:
            if not self._message_buffer:
                return

            if self.bot.is_closed() or not self.bot.is_ready():
                return

            channel = self.bot.get_channel(self.channel_id)
            if not channel:
                # Log to console that channel is not found
                print(
                    f"❌ Discord log channel {self.channel_id} not found. Clearing {len(self._message_buffer)} buffered logs.")
                self._message_buffer.clear()
                return

            messages_to_send = self._message_buffer[:]
            self._message_buffer.clear()

            for msg_content in messages_to_send:
                try:
                    # Import discord here if not imported globally in bot.py, to catch HTTPException
                    import discord # Add this line
                    for chunk in self._chunk_message(msg_content, 1900):
                        await channel.send(f"```{chunk}```")
                        await asyncio.sleep(0.7)
                except discord.HTTPException as e:
                    print(f"❌ Discord HTTP error sending log chunk: {e}")
                except Exception as e:
                    print(f"❌ Failed to send log to Discord channel: {e}")

    def _chunk_message(self, msg, max_length):
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


def get_logger(name: str, level=logging.INFO, bot=None, discord_log_channel_id=None) -> logging.Logger:
    logger = logging.getLogger(name)
    logger.setLevel(level)

    root_logger = logging.getLogger()
    # Ensure root_logger processes messages at least at the specified level
    root_logger.setLevel(logging.INFO) # Set root level to INFO or higher

    # --- Add Console Handler to the ROOT logger (if not present) ---
    # This ensures all messages (not just from a specific named logger) go to console.
    # It also helps confirm that logging is working from the root.
    if not any(isinstance(h, logging.StreamHandler) for h in root_logger.handlers):
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setFormatter(LOGGING_FORMATTER)
        root_logger.addHandler(console_handler)

    # --- Configure the ROOT logger's TimedRotatingFileHandler only ONCE ---
    # This is crucial for ensuring all logs go to the file.
    if not any(isinstance(h, TimedRotatingFileHandler) and h.baseFilename == str(LOG_FILE_PATH) for h in
               root_logger.handlers):
        # We explicitly set 'delay=False' to try and force immediate writing, though it's not a silver bullet
        file_handler = TimedRotatingFileHandler(
            filename=str(LOG_FILE_PATH),
            when="midnight",
            interval=1,
            backupCount=30,
            encoding='utf-8',
            utc=False,
            delay=False # Try to open the file immediately, not on first emit
        )
        file_handler.suffix = "%Y-%m-%d"
        file_handler.setFormatter(LOGGING_FORMATTER)
        root_logger.addHandler(file_handler)

    # --- Discord handler: Add to the ROOT logger if bot instance is provided and not already present ---
    if bot and discord_log_channel_id:
        if not any(isinstance(h, DiscordLogHandler) for h in root_logger.handlers):
            discord_handler = DiscordLogHandler(bot, discord_log_channel_id)
            discord_handler.setLevel(logging.WARNING) # Only WARNING and higher go to Discord by default
            discord_handler.setFormatter(LOGGING_FORMATTER)
            root_logger.addHandler(discord_handler)

    # Ensure this named logger propagates messages up to the root logger
    # This is essential for the root_logger's handlers to capture messages from this logger.
    logger.propagate = True

    return logger

# --- Global Logging Configuration (Applies to all loggers, including discord.py's) ---
# Ensure root logger's level is set early.
# This should be done once when the logging module is configured.
logging.getLogger().setLevel(logging.INFO)

# Explicitly set discord.py's logger level
logging.getLogger('discord').setLevel(logging.INFO)
# utils/logger.py
import logging
import sys
import pathlib
import asyncio
from logging.handlers import TimedRotatingFileHandler

BASE_DIR = pathlib.Path(__file__).parent.parent.resolve()
LOG_DIR = BASE_DIR / "logs"
LOG_DIR.mkdir(exist_ok=True, parents=True)


class DiscordLogHandler(logging.Handler):
    def __init__(self, bot, channel_id):
        super().__init__()
        self.bot = bot
        self.channel_id = channel_id
        # Buffer for messages to handle rate limits
        self._message_buffer = []
        self._send_task = None
        self._buffer_lock = asyncio.Lock()  # Use an asyncio lock for buffer access

    def emit(self, record):
        try:
            msg = self.format(record)
            # Use asyncio.run_coroutine_threadsafe for thread safety if emit is called from non-async context
            # Or simply ensure_future if you are sure emit is always called from async loop
            # For simplicity, sticking to ensure_future as it's common in discord bots
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
                # Bot not ready, keep messages in buffer for next attempt
                return

            channel = self.bot.get_channel(self.channel_id)
            if not channel:
                # Channel not found, log to console and clear buffer to prevent infinite loop
                print(
                    f"❌ Discord log channel {self.channel_id} not found. Clearing {len(self._message_buffer)} buffered logs.")
                self._message_buffer.clear()
                return

            messages_to_send = self._message_buffer[:]  # Copy buffer
            self._message_buffer.clear()  # Clear original buffer

            for msg_content in messages_to_send:
                try:
                    for chunk in self._chunk_message(msg_content, 1900):
                        await channel.send(f"```{chunk}```")
                        await asyncio.sleep(0.7)  # Small delay to respect Discord rate limits
                except discord.HTTPException as e:
                    print(f"❌ Discord HTTP error sending log chunk: {e}")
                    # Optionally re-add to buffer or log to file if Discord fails
                except Exception as e:
                    print(f"❌ Failed to send log to Discord channel: {e}")
                    # Optionally re-add to buffer or log to file if Discord fails

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

    # --- IMPORTANT: Do NOT clear all handlers indiscriminately ---
    # if logger.hasHandlers():
    #     logger.handlers.clear()

    formatter = logging.Formatter(
        "[%(asctime)s] [%(levelname)-7s] [%(name)s] %(message)s",  # Added -7s for consistent spacing
        "%Y-%m-%d %H:%M:%S"
    )

    # --- Console handler: Add to THIS specific logger if not already present ---
    # This ensures each named logger (e.g., "기본 로그", "환영-인사") has its own console output
    if not any(isinstance(h, logging.StreamHandler) for h in logger.handlers):
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setFormatter(formatter)
        logger.addHandler(console_handler)

    # --- File handler: Add to the ROOT logger if not already present ---
    # This ensures ALL logs (including discord.py's) go to log.log
    root_logger = logging.getLogger()
    file_path = LOG_DIR / "log.log"

    # Check if a TimedRotatingFileHandler for this specific file path is already attached to the root logger
    if not any(
            isinstance(h, TimedRotatingFileHandler) and h.baseFilename == str(file_path) for h in root_logger.handlers):
        file_handler = TimedRotatingFileHandler(
            filename=str(file_path),
            when="midnight",
            interval=1,
            backupCount=30,
            encoding='utf-8',
            utc=False,
        )
        file_handler.suffix = "%Y-%m-%d"
        file_handler.setFormatter(formatter)
        root_logger.addHandler(file_handler)  # <--- Added to ROOT logger

    # --- Discord handler: Add to THIS specific logger if not already present ---
    # If you want all logs to go to Discord, add this to root_logger too
    if bot and discord_log_channel_id:
        if not any(isinstance(h, DiscordLogHandler) for h in logger.handlers):
            discord_handler = DiscordLogHandler(bot, discord_log_channel_id)
            discord_handler.setFormatter(formatter)
            logger.addHandler(discord_handler)

    # Ensure this logger propagates messages up to the root logger
    # This is crucial for the root logger's file handler to capture messages from this logger.
    logger.propagate = True

    return logger


# --- Global Logging Configuration (Applies to all loggers, including discord.py's) ---
# Set the root logger's level. All messages at or above this level will be processed.
logging.getLogger().setLevel(logging.INFO)

# Explicitly set discord.py's logger level
logging.getLogger('discord').setLevel(logging.INFO)
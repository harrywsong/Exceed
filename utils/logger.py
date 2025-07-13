import logging
import sys
import asyncio
import io
import os
from datetime import datetime
import pathlib

# Fix Windows UTF-8 console output:
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

BASE_DIR = pathlib.Path(__file__).parent.parent.resolve()  # Adjust if needed
LOG_DIR = BASE_DIR / "logs"
LOG_DIR.mkdir(exist_ok=True, parents=True)  # Ensure all parents exist

class DiscordLogHandler(logging.Handler):
    def __init__(self, bot, channel_id):
        super().__init__()
        self.bot = bot
        self.channel_id = channel_id

    def emit(self, record):
        try:
            msg = self.format(record)
            # Use asyncio.ensure_future for compatibility and safety
            asyncio.ensure_future(self.send_log(msg))
        except Exception:
            self.handleError(record)

    async def send_log(self, msg):
        if self.bot.is_closed():
            # Bot is shutting down or closed, avoid sending
            return
        channel = self.bot.get_channel(self.channel_id)
        if channel:
            try:
                # Discord message length limit is 2000 characters
                for chunk in self._chunk_message(msg, 1900):
                    await channel.send(f"```{chunk}```")
            except Exception as e:
                print(f"Failed to send log to Discord channel: {e}")

    def _chunk_message(self, msg, max_length):
        # Splits the message into chunks smaller than max_length
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

class FlushFileHandler(logging.FileHandler):
    def emit(self, record):
        super().emit(record)
        self.flush()

def get_logger(name: str, level=logging.INFO, bot=None, discord_log_channel_id=None) -> logging.Logger:
    logger = logging.getLogger(name)
    logger.setLevel(level)

    if logger.hasHandlers():
        logger.handlers.clear()

    formatter = logging.Formatter(
        "[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s",
        "%Y-%m-%d %H:%M:%S"
    )

    # Console output
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    # Daily file log with flushing and absolute path
    date_str = datetime.now().strftime("%Y-%m-%d")
    file_path = LOG_DIR / f"{date_str}.log"
    file_handler = FlushFileHandler(str(file_path), encoding='utf-8')
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    # Discord handler (optional)
    if bot and discord_log_channel_id:
        discord_handler = DiscordLogHandler(bot, discord_log_channel_id)
        discord_handler.setFormatter(formatter)
        logger.addHandler(discord_handler)

    logger.propagate = True
    return logger

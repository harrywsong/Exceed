import logging
import sys
from datetime import datetime
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

    def emit(self, record):
        try:
            msg = self.format(record)
            asyncio.ensure_future(self.send_log(msg))
        except Exception:
            self.handleError(record)

    async def send_log(self, msg):
        if self.bot.is_closed():
            return
        channel = self.bot.get_channel(self.channel_id)
        if channel:
            try:
                for chunk in self._chunk_message(msg, 1900):
                    await channel.send(f"```{chunk}```")
            except Exception as e:
                print(f"Failed to send log to Discord channel: {e}")

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

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    # Use TimedRotatingFileHandler to rotate logs at midnight
    file_path = LOG_DIR / "log.log"
    file_handler = TimedRotatingFileHandler(
        filename=str(file_path),
        when="midnight",
        interval=1,
        backupCount=30,  # keep logs for 30 days, adjust as needed
        encoding='utf-8',
        utc=False,
    )
    file_handler.setFormatter(formatter)

    # Set the suffix pattern for rotated log files (e.g., log.log.2025-07-12)
    file_handler.suffix = "%Y-%m-%d"

    # The extMatch attribute is an internal regex used by TimedRotatingFileHandler to find old log files.
    # We set it manually here to avoid AttributeError, matching the suffix pattern above.
    import re
    file_handler.extMatch = re.compile(r"^\d{4}-\d{2}-\d{2}$")

    logger.addHandler(file_handler)

    if bot and discord_log_channel_id:
        discord_handler = DiscordLogHandler(bot, discord_log_channel_id)
        discord_handler.setFormatter(formatter)
        logger.addHandler(discord_handler)

    logger.propagate = True  # Let child loggers propagate to this root

    return logger

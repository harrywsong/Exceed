import logging
import sys
import asyncio
import io

# Fix Windows UTF-8 console output:
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

class DiscordLogHandler(logging.Handler):
    def __init__(self, bot, channel_id):
        super().__init__()
        self.bot = bot
        self.channel_id = channel_id

    def emit(self, record):
        try:
            msg = self.format(record)
            # Schedule async send (fire-and-forget)
            asyncio.create_task(self.send_log(msg))
        except Exception:
            self.handleError(record)

    async def send_log(self, msg):
        channel = self.bot.get_channel(self.channel_id)
        if channel:
            try:
                await channel.send(f"```{msg}```")
            except Exception as e:
                print(f"Failed to send log to Discord channel: {e}")

def get_logger(name: str, log_file: str = None, level=logging.INFO, bot=None, discord_log_channel_id=None) -> logging.Logger:
    logger = logging.getLogger(name)
    logger.setLevel(level)

    # Clear handlers to prevent closed file errors & duplicates
    if logger.hasHandlers():
        logger.handlers.clear()

    formatter = logging.Formatter(
        "[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s",
        "%Y-%m-%d %H:%M:%S"
    )

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    if log_file:
        file_handler = logging.FileHandler(log_file, encoding='utf-8')
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

    if bot and discord_log_channel_id:
        discord_handler = DiscordLogHandler(bot, discord_log_channel_id)
        discord_handler.setFormatter(formatter)
        logger.addHandler(discord_handler)

    logger.propagate = False  # Avoid propagation to root handlers that might be closed

    return logger

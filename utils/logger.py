import logging
import sys
import pathlib
import asyncio
from logging.handlers import TimedRotatingFileHandler
import discord

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
        self._send_task = None
        self._buffer_lock = asyncio.Lock()

        self.setLevel(logging.WARNING)

    def emit(self, record):
        log_entry = self.format(record)
        try:
            asyncio.ensure_future(self._add_to_buffer(log_entry))
        except RuntimeError:
            print(f"DEBUG: No running event loop for DiscordHandler. Buffering for later: {log_entry}", file=sys.stderr)
            self._message_buffer.append(log_entry)

    async def _add_to_buffer(self, msg):
        async with self._buffer_lock:
            self._message_buffer.append(msg)
            if self._send_task is None or self._send_task.done():
                self._send_task = asyncio.create_task(self._send_buffered_logs())

    async def _send_buffered_logs(self):
        await self.bot.wait_until_ready()

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
                        await asyncio.sleep(0.7)
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
        discord_handler.setLevel(logging.DEBUG)
        discord_handler.setFormatter(LOGGING_FORMATTER)
        root_logger.addHandler(discord_handler)

def get_logger(name: str, level=logging.INFO, bot=None, discord_log_channel_id=None) -> logging.Logger:
    """
    Retrieves a logger with the specified name and level.
    The DiscordHandler is now managed by the root logger configuration.
    """
    logger = logging.getLogger(name)
    logger.setLevel(level)
    logger.propagate = True
    return logger

logging.getLogger('discord').setLevel(logging.INFO)
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
        # Initialize _bot_ready_event from the bot instance's ready_event
        self._bot_ready_event = bot.ready_event # <-- **CRITICAL ADDITION**

        self.setLevel(logging.WARNING)

    def emit(self, record):
        log_entry = self.format(record)
        try:
            # Schedule the coroutine on the bot's event loop
            if self.bot and self.bot.loop and self.bot.loop.is_running():
                asyncio.run_coroutine_threadsafe(self._add_to_buffer(log_entry), self.bot.loop)
            else:
                # Fallback if the event loop isn't running yet (very early startup)
                self._message_buffer.append(log_entry)
        except Exception as e:
            # Catching general exceptions during scheduling for robustness
            print(f"ERROR: Failed to schedule log to Discord buffer: {e} - Log: {log_entry}", file=sys.stderr)
            self._message_buffer.append(log_entry) # Try to buffer anyway


    async def _add_to_buffer(self, msg):
        async with self._buffer_lock:
            self._message_buffer.append(msg)
            if self._send_task is None or self._send_task.done():
                self._send_task = asyncio.create_task(self._send_buffered_logs())

    async def _send_buffered_logs(self):
        # Wait until the bot explicitly signals it's ready using its event
        await self._bot_ready_event.wait() # <-- **CRITICAL CHANGE**

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
                except discord.HTTPException as e:
                    print(f"❌ Discord HTTP error sending log chunk: {e} (Status: {e.status})", file=sys.stderr)
                except Exception as e:
                    print(f"❌ Failed to send log to Discord channel: {e}", file=sys.stderr)

    def _chunk_message(self, msg, max_length):
        """Splits a message into chunks that fit Discord's character limit."""
        lines = msg.splitlines(keepends=True)
        chunk = ""
        for line in lines:
            if len(chunk) + len(line) > max_length and chunk:
                yield chunk
                chunk = ""
            while len(line) > max_length:
                yield line[:max_length]
                line = line[max_length:]
            chunk += line
        if chunk:
            yield chunk


def _configure_root_handlers(bot=None, discord_log_channel_id=None):
    """
    Configures or re-configures the root logger's file, console, and Discord handlers.
    This function is crucial for re-establishing handlers after log file
    renaming operations (e.g., crash log upload) and for initial setup.
    """
    handlers_to_remove = root_logger.handlers[:]
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

def get_logger(name: str, level=logging.INFO) -> logging.Logger: # Removed bot and discord_log_channel_id here
    """
    Retrieves a logger with the specified name and level.
    The DiscordHandler is now managed by the root logger configuration.
    """
    logger = logging.getLogger(name)
    logger.setLevel(level)
    logger.propagate = True
    return logger

logging.getLogger('discord').setLevel(logging.INFO)
logging.getLogger('discord.http').setLevel(logging.INFO)
logging.getLogger('websockets').setLevel(logging.INFO)
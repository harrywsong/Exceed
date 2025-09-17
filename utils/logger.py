# /home/hws/Exceed/utils/logger.py

import logging
import sys
import pathlib
import asyncio
import threading
from logging.handlers import TimedRotatingFileHandler
import discord
from utils import config
import os

# Define file paths and formatters
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
    A custom logging handler to send log messages to a Discord channel.
    It buffers messages and sends them asynchronously.
    Multi-server compatible - it routes logs based on `guild_id` in `extra`.
    """

    def __init__(self, bot):
        super().__init__()
        self.bot = bot
        self._message_buffer = []
        self._send_task = None
        self._buffer_lock = threading.Lock()
        self.stopped = False
        self.channel_cache = {}

    def _get_log_channel(self, guild_id: int = None) -> discord.TextChannel | None:
        """Find the log channel, prioritizing a specific guild's channel if available."""
        # 1. Check cache for a specific guild channel
        if guild_id and guild_id in self.channel_cache:
            return self.channel_cache[guild_id]

        # 2. Look up channel for a specific guild from config
        if guild_id:
            channel_id = config.get_channel_id(guild_id, 'log_channel')
            if channel_id:
                channel = self.bot.get_channel(channel_id)
                if channel:
                    self.channel_cache[guild_id] = channel
                    return channel

        # 3. Fallback to the global log channel from environment variables
        global_log_channel_id_str = os.getenv("DISCORD_LOG_CHANNEL_ID")
        global_log_channel_id = int(global_log_channel_id_str) if global_log_channel_id_str else None

        if global_log_channel_id and 0 not in self.channel_cache:
            global_channel = self.bot.get_channel(global_log_channel_id)
            if global_channel:
                self.channel_cache[0] = global_channel  # Cache with a special key
                return global_channel
        elif 0 in self.channel_cache:
            return self.channel_cache[0]

        return None

    def emit(self, record):
        """
        Emit a log record. This method is called synchronously. We extract guild_id
        from the record and buffer the message.
        """
        log_entry = self.format(record)
        if self.stopped:
            return

        guild_id = getattr(record, 'guild_id', None)

        with self._buffer_lock:
            self._message_buffer.append({'guild_id': guild_id, 'message': log_entry})

    def start_sending_logs(self):
        """
        Starts the asynchronous task to send buffered logs to Discord.
        """
        if self._send_task is None or self._send_task.done():
            self._send_task = self.bot.loop.create_task(self._send_buffered_logs())

    async def _send_buffered_logs(self):
        """Periodically sends buffered logs to Discord."""
        try:
            await self.bot.wait_until_ready()
        except RuntimeError:
            return

        while not self.stopped:
            try:
                await asyncio.sleep(5)
                messages_to_send = []
                with self._buffer_lock:
                    if self._message_buffer:
                        messages_to_send.extend(self._message_buffer)
                        self._message_buffer.clear()

                if not messages_to_send:
                    continue

                # Group logs by guild_id to send them to the correct channel
                guild_logs = {}
                for item in messages_to_send:
                    guild_id = item['guild_id']
                    message = item['message']
                    if guild_id not in guild_logs:
                        guild_logs[guild_id] = []
                    guild_logs[guild_id].append(message)

                for guild_id, msgs in guild_logs.items():
                    channel = self._get_log_channel(guild_id)
                    if not channel:
                        if len(msgs) > 0:
                            print(
                                f"Discord log channel not available for guild {guild_id}. Clearing {len(msgs)} buffered logs.",
                                file=sys.stderr)
                        continue

                    full_message = "\n".join(msgs)
                    for chunk in self._chunk_message(full_message, 1900):
                        try:
                            await channel.send(f"```\n{chunk}\n```")
                            await asyncio.sleep(0.7)  # Add a small delay to prevent rate limits
                        except discord.Forbidden:
                            print(f"DiscordHandler: Missing permissions for channel {channel.id}.", file=sys.stderr)
                            break
                        except Exception as e:
                            print(f"Failed to send log to Discord channel: {e}", file=sys.stderr)

            except asyncio.CancelledError:
                break
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

    def close(self):
        self.stopped = True
        if self._send_task and not self._send_task.done():
            self._send_task.cancel()
        self._send_task = None
        super().close()


def setup_logging(bot=None):
    """
    Configures or re-configures the root logger's file, console, and Discord handlers.
    This function should be called with the bot instance once it's ready.
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

    if bot:
        discord_handler = DiscordHandler(bot)
        discord_handler.setLevel(logging.INFO)
        discord_handler.setFormatter(LOGGING_FORMATTER)
        root_logger.addHandler(discord_handler)
        discord_handler.start_sending_logs()


def get_logger(name: str, level=logging.INFO) -> logging.Logger:
    """Retrieves a logger with the specified name and level."""
    logger = logging.getLogger(name)
    logger.setLevel(level)
    logger.propagate = True
    return logger

import logging.handlers

def close_log_handlers():
    """Closes all file handlers to release file locks."""
    for handler in root_logger.handlers[:]:
        if isinstance(handler, logging.handlers.TimedRotatingFileHandler):
            handler.close()
            root_logger.removeHandler(handler)

logging.getLogger('discord').setLevel(logging.INFO)
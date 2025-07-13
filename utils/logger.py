# /home/hws/Exceed/utils/logger.py

import logging
import sys
import pathlib
import asyncio
from logging.handlers import TimedRotatingFileHandler
import discord # Ensure discord is imported

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

    def __init__(self, bot, channel_id, level=logging.INFO): # Added level parameter
        super().__init__()
        self.bot = bot
        self.channel_id = channel_id
        self._message_buffer = []
        self._send_task = None  # Task is initially None, started when bot is ready
        self._buffer_lock = asyncio.Lock()
        self.stopped = False # Flag to indicate if the handler is closing

        self.setLevel(level) # Set level based on parameter, default INFO

    def emit(self, record):
        log_entry = self.format(record)
        if self.stopped: # Don't buffer if closing
            return
        # Directly add to buffer. The _send_task will pick it up when running.
        # Use a simple append if no loop is running yet to avoid RuntimeError
        # or if the bot is not yet ready.
        try:
            # If bot loop is running, use run_coroutine_threadsafe for thread safety
            if self.bot and self.bot.loop and not self.bot.loop.is_closed():
                asyncio.run_coroutine_threadsafe(self._add_to_buffer(log_entry), self.bot.loop)
            else:
                # Fallback for very early startup before bot.loop is active
                self._message_buffer.append(log_entry)
        except RuntimeError:
            # This can happen if bot.loop is not yet set up or is closed
            self._message_buffer.append(log_entry)


    async def _add_to_buffer(self, msg):
        """Adds a log entry to the buffer safely."""
        async with self._buffer_lock:
            self._message_buffer.append(msg)

    async def _send_buffered_logs(self):
        """
        Periodically sends buffered log messages to Discord.
        This task must only be started AFTER the bot is ready.
        """
        # Ensure the bot is ready before doing anything Discord-related
        try:
            await self.bot.wait_until_ready()
        except asyncio.CancelledError:
            # If task is cancelled while waiting, just exit
            return
        except Exception as e:
            # Log any other unexpected errors during wait_until_ready
            print(f"DiscordHandler: Error waiting for bot to be ready: {e}", file=sys.stderr)
            return

        print("DiscordHandler: Bot is ready, starting to send buffered logs.") # Debug print

        while not self.stopped:
            try:
                await asyncio.sleep(5)  # Adjust sending interval as needed

                messages_to_send = []
                async with self._buffer_lock:
                    if not self._message_buffer:
                        continue # Nothing to send

                    # Take a batch of messages from the buffer
                    messages_to_send = self._message_buffer[:10] # Send up to 10 messages at once
                    self._message_buffer = self._message_buffer[10:]

                if not messages_to_send:
                    continue

                full_message = "```\n" + "\n".join(messages_to_send) + "\n```"
                try:
                    if len(full_message) > 2000:
                        # Discord message limit is 2000 characters. Split if necessary.
                        for i in range(0, len(full_message), 1990):
                            await channel.send(full_message[i:i+1990])
                            await asyncio.sleep(0.5) # Small delay between parts
                    else:
                        channel = self.bot.get_channel(self.channel_id)
                        if not channel:
                            print(f"❌ DiscordHandler: 로그 채널 ID {self.channel_id}을(를) 찾을 수 없습니다. 버퍼링된 로그 {len(messages_to_send)}개 지움.", file=sys.stderr)
                            # Clear buffer if channel not found to prevent endless loop
                            async with self._buffer_lock:
                                self._message_buffer.clear()
                            continue # Skip to next iteration
                        await channel.send(full_message)
                    # self.logger.debug(f"DiscordHandler: {len(messages_to_send)}개의 로그 메시지 전송됨.")
                except discord.Forbidden:
                    print(f"❌ DiscordHandler: 채널 {self.channel_id}에 메시지를 보낼 권한이 없습니다. 버퍼링된 로그 {len(messages_to_send)}개 지움.", file=sys.stderr)
                    # Clear buffer if we can't send, to prevent endless loop of unsent messages
                    async with self._buffer_lock:
                        self._message_buffer.clear()
                    break # Exit the loop if permissions are an issue
                except discord.HTTPException as e:
                    print(f"❌ Discord HTTP error sending log chunk: {e}", file=sys.stderr)
                    await asyncio.sleep(5) # Wait before retrying on HTTP error
                except Exception as e:
                    print(f"❌ DiscordHandler: 로그 메시지 전송 중 알 수 없는 오류 발생: {e}", file=sys.stderr)
                    await asyncio.sleep(5) # Wait before retrying on unknown error

            except asyncio.CancelledError:
                print("DiscordHandler: _send_buffered_logs task cancelled.")
                break # Exit the loop if cancelled
            except Exception as e:
                print(f"DiscordHandler: Unexpected error in send loop: {e}", file=sys.stderr)


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

    def start_sending_logs(self):
        """
        Starts the asynchronous task to send buffered logs to Discord.
        This should be called once the bot is ready and its loop is running.
        """
        if self._send_task is None or self._send_task.done():
            self._send_task = self.bot.loop.create_task(self._send_buffered_logs())
            print("DiscordHandler: Log sending task created and started.") # Debug print

    def close(self):
        self.stopped = True # Signal the task to stop
        if self._send_task and not self._send_task.done():
            self._send_task.cancel()
            # In a clean shutdown, you might want to await its completion:
            # try:
            #     asyncio.get_event_loop().run_until_complete(self._send_task)
            # except:
            #     pass
        self._send_task = None
        super().close()


def _configure_root_handlers(bot=None, discord_log_channel_id=None, console_level=logging.INFO, file_level=logging.INFO, discord_level=logging.INFO):
    """
    Configures the root logger with file, console, and optional Discord handlers.
    This function should be called once after the bot is initialized.
    """
    # Get the root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO) # Ensure root logger is at least INFO

    # Remove existing handlers to prevent duplicates on reload/reconfiguration
    handlers_to_remove = []
    for handler in root_logger.handlers:
        # If it's a DiscordHandler, ensure it's stopped before removal
        if isinstance(handler, DiscordHandler):
            handler.close()
        handlers_to_remove.append(handler)

    for handler in handlers_to_remove:
        try:
            root_logger.removeHandler(handler)
        except Exception as e:
            print(f"Error removing handler {type(handler).__name__}: {e}", file=sys.stderr)

    # File Handler
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
    file_handler.setLevel(file_level) # Set level for file handler
    root_logger.addHandler(file_handler)

    # Console Handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(CONSOLE_FORMATTER)
    console_handler.setLevel(console_level) # Set level for console handler
    root_logger.addHandler(console_handler)

    # Discord Handler (only if bot and channel_id are provided)
    if bot and discord_log_channel_id:
        discord_handler = DiscordHandler(bot, discord_log_channel_id, level=discord_level) # Pass level
        discord_handler.setFormatter(LOGGING_FORMATTER)
        root_logger.addHandler(discord_handler)
        # The start_sending_logs() call is moved to bot.py's on_ready event.


def get_logger(name: str, level=logging.INFO, **kwargs) -> logging.Logger:
    """
    Retrieves a logger with the specified name and level.
    The DiscordHandler is now managed by the root logger configuration.
    Accepts **kwargs to allow for backward compatibility with old cog calls
    that might pass 'bot' or 'discord_log_channel_id'. These arguments are
    ignored by this function as handler configuration is done globally.
    """
    logger = logging.getLogger(name)
    logger.setLevel(level)
    logger.propagate = True # Allow logs to propagate to root handlers (including DiscordHandler)
    return logger

# Set discord.py's internal logger level
logging.getLogger('discord').setLevel(logging.INFO)


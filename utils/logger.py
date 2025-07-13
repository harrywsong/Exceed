# /home/hws/Exceed/utils/logger.py

import logging
import sys
import pathlib
import asyncio
from logging.handlers import TimedRotatingFileHandler
import discord  # Ensure discord is imported

LOG_FILE_PATH = pathlib.Path(__file__).parent.parent / "logs" / "log.log"
CRASH_LOG_FILE = pathlib.Path(
    __file__).parent.parent / "logs" / "crash_log.txt"  # Define crash log path here too for clarity
LOG_FILE_PATH.parent.mkdir(parents=True, exist_ok=True)
CRASH_LOG_FILE.parent.mkdir(parents=True, exist_ok=True)

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
        self._send_task = None  # Task is initially None, started when bot is ready
        self._buffer_lock = asyncio.Lock()
        self.stopped = False  # Flag to indicate if the handler is closing

        # The level for this specific handler is set in _configure_root_handlers
        # self.setLevel(logging.WARNING) # This line is now effectively managed externally

    def emit(self, record):
        log_entry = self.format(record)
        if self.stopped:  # Don't buffer if closing
            print(f"DEBUG: DiscordHandler stopped. Dropping log: {log_entry}", file=sys.stderr)
            return
        try:
            # If bot loop is running, use run_coroutine_threadsafe for thread safety
            # This ensures logs from other threads (like Flask API) are handled
            if self.bot and self.bot.loop and not self.bot.loop.is_closed():
                asyncio.run_coroutine_threadsafe(self._add_to_buffer(log_entry), self.bot.loop)
            else:
                # Fallback for very early startup before bot.loop is active
                self._message_buffer.append(log_entry)
                print(f"DEBUG: No running event loop for DiscordHandler. Buffering for later: {log_entry}",
                      file=sys.stderr)
        except RuntimeError as e:
            # This can happen if bot.loop is not yet set up or is closed
            self._message_buffer.append(log_entry)
            print(f"DEBUG: RuntimeError in emit, buffering: {log_entry} - {e}", file=sys.stderr)

    async def _add_to_buffer(self, msg):
        async with self._buffer_lock:
            self._message_buffer.append(msg)
            # print(f"DEBUG: Message added to buffer. Current buffer size: {len(self._message_buffer)}", file=sys.stderr) # Too verbose

    async def _send_buffered_logs(self):
        """
        Periodically sends buffered log messages to Discord.
        This task must only be started AFTER the bot is ready.
        """
        print("DEBUG: _send_buffered_logs task started. Waiting for bot to be ready...", file=sys.stderr)
        try:
            await self.bot.wait_until_ready()
        except asyncio.CancelledError:
            print("DEBUG: _send_buffered_logs task cancelled while waiting for bot.", file=sys.stderr)
            return
        except Exception as e:
            print(f"ERROR: DiscordHandler: Error waiting for bot to be ready: {e}", file=sys.stderr)
            return

        print("DEBUG: DiscordHandler: Bot is ready, starting to send buffered logs.", file=sys.stderr)  # Debug print

        while not self.stopped:
            try:
                await asyncio.sleep(5)  # Adjust sending interval as needed

                messages_to_send = []
                async with self._buffer_lock:
                    if not self._message_buffer:
                        print("DEBUG: DiscordHandler buffer is empty. Skipping send cycle.",
                              file=sys.stderr)  # Added debug
                        continue  # Nothing to send

                    # Take a batch of messages from the buffer
                    messages_to_send = self._message_buffer[:10]  # Send up to 10 messages at once
                    self._message_buffer = self._message_buffer[10:]

                if not messages_to_send:
                    print(
                        "DEBUG: messages_to_send list is empty after taking from buffer. This should not happen if buffer was not empty.",
                        file=sys.stderr)  # Added debug
                    continue

                full_message = "```\n" + "\n".join(messages_to_send) + "\n```"

                print(
                    f"DEBUG: Attempting to send {len(messages_to_send)} log messages to Discord. Message length: {len(full_message)}",
                    file=sys.stderr)  # Added debug

                try:
                    channel = self.bot.get_channel(self.channel_id)
                    if not channel:
                        print(
                            f"ERROR: DiscordHandler: 로그 채널 ID {self.channel_id}을(를) 찾을 수 없습니다. 버퍼링된 로그 {len(messages_to_send)}개 지움.",
                            file=sys.stderr)
                        # Clear buffer if we can't find channel, to prevent endless loop of unsent messages
                        async with self._buffer_lock:  # Re-acquire lock to clear the buffer safely
                            self._message_buffer.clear()
                        break  # Exit the loop if channel is permanently unavailable

                    if len(full_message) > 2000:
                        # Discord message limit is 2000 characters. Split if necessary.
                        print(f"DEBUG: Message too long ({len(full_message)} chars). Chunking and sending.",
                              file=sys.stderr)  # Added debug
                        for i, chunk in enumerate(self._chunk_message(full_message, 1990)):
                            await channel.send(chunk)
                            if i < len(full_message) / 1990 - 1:  # Don't sleep after the last chunk
                                await asyncio.sleep(0.7)  # Small delay between parts
                    else:
                        await channel.send(full_message)
                    print(f"DEBUG: Successfully sent {len(messages_to_send)} log messages to Discord.", file=sys.stderr)

                except discord.Forbidden:
                    print(
                        f"ERROR: DiscordHandler: 채널 {self.channel_id}에 메시지를 보낼 권한이 없습니다. 버퍼링된 로그 {len(messages_to_send)}개 지움.",
                        file=sys.stderr)
                    # Clear buffer if we can't send, to prevent endless loop of unsent messages
                    async with self._buffer_lock:  # Re-acquire lock to clear the buffer safely
                        self._message_buffer.clear()
                    break  # Exit the loop if permissions are an issue
                except discord.HTTPException as e:
                    print(f"ERROR: Discord HTTP 오류 로그 전송: {e}", file=sys.stderr)
                    await asyncio.sleep(5)  # Wait before retrying on HTTP error
                except Exception as e:
                    print(f"CRITICAL: DiscordHandler: 로그 메시지 전송 중 알 수 없는 오류 발생: {e}", file=sys.stderr)
                    await asyncio.sleep(5)  # Wait before retrying on unknown error

            except asyncio.CancelledError:
                print("DEBUG: _send_buffered_logs 작업이 취소되었습니다.", file=sys.stderr)
                break  # Exit the loop if cancelled
            except Exception as e:
                print(f"CRITICAL: _send_buffered_logs 전송 루프에서 예상치 못한 오류 발생: {e}", file=sys.stderr)

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
        This should be called once the bot is ready.
        """
        if self._send_task is None or self._send_task.done():
            self._send_task = asyncio.create_task(self._send_buffered_logs())
            print("DEBUG: DiscordHandler: 로그 전송 작업이 생성되고 시작되었습니다.")  # Debug print

    def close(self):
        self.stopped = True  # Signal the task to stop
        if self._send_task and not self._send_task.done():
            self._send_task.cancel()
            # In a clean shutdown, you might want to await its completion:
            # try:
            #     asyncio.get_event_loop().run_until_complete(self._send_task)
            # except:
            #     pass
        self._send_task = None
        super().close()


def _configure_root_handlers(bot=None, discord_log_channel_id=None, console_level=logging.INFO, file_level=logging.INFO,
                             discord_level=logging.INFO):  # Changed discord_level to INFO
    """
    Configures the root logger with file, console, and optional Discord handlers.
    This function should be called once after the bot is initialized.
    """
    # Get the root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)  # Ensure root logger is at least INFO

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
            print(f"핸들러 제거 오류 {type(handler).__name__}: {e}", file=sys.stderr)

    # File Handler (changed from TimedRotatingFileHandler)
    file_handler = TimedRotatingFileHandler(  # Changed back to TimedRotatingFileHandler
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
    file_handler.setLevel(file_level)  # Set level for file handler
    root_logger.addHandler(file_handler)

    # Console Handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(CONSOLE_FORMATTER)
    console_handler.setLevel(console_level)  # Set level for console handler
    root_logger.addHandler(console_handler)

    # Discord Handler (only if bot and channel_id are provided)
    if bot and discord_log_channel_id:
        discord_handler = DiscordHandler(bot, discord_log_channel_id)
        discord_handler.setFormatter(LOGGING_FORMATTER)
        discord_handler.setLevel(discord_level)  # Set level for discord handler
        root_logger.addHandler(discord_handler)
        # Removed discord_handler.start_sending_logs() from here.
        # It will now be explicitly called from bot.py's on_ready event.


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
    logger.propagate = True  # Allow logs to propagate to root handlers (including DiscordHandler)
    return logger


# Set discord.py's internal logger level
logging.getLogger('discord').setLevel(logging.INFO)

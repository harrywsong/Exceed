import logging
import sys
import pathlib
import asyncio
from logging.handlers import TimedRotatingFileHandler
import discord

# --- Configuration ---
LOG_FILE_PATH = pathlib.Path(__file__).parent.parent / "logs" / "log.log"
LOG_FILE_PATH.parent.mkdir(parents=True, exist_ok=True)

# Define formatters for consistency
LOGGING_FORMATTER = logging.Formatter(
    "[{asctime}] [{levelname:.<8}] [{name}] {message}",
    datefmt="%Y-%m-%d %H:%M:%S",
    style="{",
)
CONSOLE_FORMATTER = LOGGING_FORMATTER # Often, console and file formats can be the same, customize if needed

# Initialize root logger
root_logger = logging.getLogger()
root_logger.setLevel(logging.INFO) # Default level for the root logger

# --- Discord Logging Handler ---
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
        # CRITICAL: Store a reference to the bot's asyncio.Event for readiness
        self._bot_ready_event = bot.ready_event # This is the event MyBot sets in on_ready

        self.setLevel(logging.WARNING) # Default logging level for messages sent to Discord

    def emit(self, record):
        """
        Emits a log record. This method is called by the logging system.
        It buffers the message and attempts to schedule sending to Discord.
        Handles cases where the asyncio loop isn't yet running.
        """
        log_entry = self.format(record)
        # Attempt to get the event loop. If it's not running, buffer for later.
        try:
            loop = self.bot.loop # Get the bot's event loop
            if loop.is_running():
                # Schedule the async task on the bot's running event loop
                asyncio.run_coroutine_threadsafe(self._add_to_buffer(log_entry), loop)
            else:
                # If the loop isn't running yet (very early startup), just buffer.
                # The _send_buffered_logs task (when it finally runs) will process these.
                self._message_buffer.append(log_entry)
        except Exception as e:
            # This catch is for issues scheduling, not for sending itself.
            # Print to stderr as a last resort, and still try to buffer.
            print(f"ERROR: DiscordHandler emit failed to schedule: {e} - Log: {log_entry}", file=sys.stderr)
            self._message_buffer.append(log_entry) # Ensure it's buffered even if scheduling fails

    async def _add_to_buffer(self, msg):
        """Adds a message to the buffer and ensures a sending task is scheduled."""
        async with self._buffer_lock:
            self._message_buffer.append(msg)
            # If no send task is active or it's finished, create a new one
            if self._send_task is None or self._send_task.done():
                self._send_task = asyncio.create_task(self._send_buffered_logs())

    async def _send_buffered_logs(self):
        """
        Waits for the bot to be ready, then sends all buffered logs to Discord.
        """
        # CRITICAL: Wait for the bot's ready_event to be set.
        # This ensures the bot is fully initialized and logged in.
        await self._bot_ready_event.wait()

        async with self._buffer_lock:
            if not self._message_buffer:
                return # No messages to send

            channel = self.bot.get_channel(self.channel_id)
            if not channel:
                print(f"❌ Discord log channel {self.channel_id} not found. Clearing {len(self._message_buffer)} buffered logs.", file=sys.stderr)
                self._message_buffer.clear()
                return

            # Take a snapshot of the buffer and clear it
            messages_to_send = self._message_buffer[:]
            self._message_buffer.clear()

            for msg_content in messages_to_send:
                try:
                    # Chunk messages to adhere to Discord's character limit
                    for chunk in self._chunk_message(msg_content, 1900): # 1900 to leave space for code block markdown
                        await channel.send(f"```\n{chunk}\n```")
                        await asyncio.sleep(0.7) # Delay to prevent hitting Discord rate limits
                except discord.Forbidden:
                    print(f"❌ Discord Forbidden: Bot lacks permissions to send messages in channel {self.channel_id}.", file=sys.stderr)
                    # Don't re-buffer, as it's a persistent permission issue
                except discord.HTTPException as e:
                    print(f"❌ Discord HTTP error sending log chunk (Status: {e.status}): {e}", file=sys.stderr)
                    # Consider re-buffering for transient HTTP errors if desired, but for now, we drop them to avoid loops
                except Exception as e:
                    print(f"❌ Failed to send log to Discord channel: {e}", file=sys.stderr)
                    # Drop other generic errors for now

    def _chunk_message(self, msg, max_length):
        """Splits a message into chunks that fit Discord's character limit."""
        lines = msg.splitlines(keepends=True) # Keep newlines for formatting
        current_chunk = ""
        for line in lines:
            # If adding the line exceeds max_length, yield current_chunk and start new
            if len(current_chunk) + len(line) > max_length and current_chunk:
                yield current_chunk.strip() # Strip to clean up leading/trailing whitespace from chunks
                current_chunk = "" # Reset for the new chunk

            # Handle lines that are themselves longer than max_length
            while len(line) > max_length:
                yield line[:max_length].strip()
                line = line[max_length:]

            current_chunk += line # Add remaining part of line (or whole line if short)

        if current_chunk: # Yield any leftover chunk
            yield current_chunk.strip()

# --- Root Logger Configuration ---
def _configure_root_handlers(bot=None, discord_log_channel_id=None):
    """
    Configures or re-configures the root logger's file, console, and Discord handlers.
    This function is crucial for re-establishing handlers after log file
    renaming operations (e.g., crash log upload) and for initial setup.
    """
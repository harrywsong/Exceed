import logging
import sys
import pathlib
import asyncio
from logging.handlers import TimedRotatingFileHandler
import discord # Ensure discord is imported for HTTPException handling in DiscordHandler

# Define log file path
# This will create 'logs/log.log' in the parent directory of utils/logger.py
LOG_FILE_PATH = pathlib.Path(__file__).parent.parent / "logs" / "log.log"
LOG_FILE_PATH.parent.mkdir(parents=True, exist_ok=True) # Ensure logs directory exists

# Define logging formatters
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

# Get the root logger
root_logger = logging.getLogger()
root_logger.setLevel(logging.INFO) # Set overall minimum level for the root logger

# Clear existing handlers from the root logger to prevent duplicates on restart
# This is crucial for systemd services that might re-initialize the script
if root_logger.handlers:
    for handler in root_logger.handlers[:]: # Iterate over a copy to safely remove
        root_logger.removeHandler(handler)

# Add TimedRotatingFileHandler to the ROOT logger
# This ensures all messages (INFO and above) go to the file.
file_handler = TimedRotatingFileHandler(
    filename=str(LOG_FILE_PATH),
    when="midnight",
    interval=1,
    backupCount=30,
    encoding='utf-8',
    utc=False,
    delay=False,
    # The 'buffering' argument was removed as it caused a TypeError
    # with the system's logging.handlers module version.
)
file_handler.suffix = "%Y-%m-%d"
file_handler.setFormatter(LOGGING_FORMATTER)
root_logger.addHandler(file_handler)

# Add a StreamHandler for console output (journalctl captures this)
# This allows you to see logs in real-time via `journalctl -f`.
console_handler = logging.StreamHandler(sys.stdout)
console_handler.setFormatter(CONSOLE_FORMATTER)
root_logger.addHandler(console_handler)


def get_logger(name: str, level=logging.INFO, bot=None, discord_log_channel_id=None) -> logging.Logger:
    """
    Retrieves a logger with the specified name and level,
    adding a Discord handler for ERROR level messages if bot and channel ID are provided.
    """
    logger = logging.getLogger(name)
    logger.setLevel(level)
    # By default, loggers propagate messages up to their parent loggers, eventually reaching the root.
    # This is desired behavior for file logging via the root handler.
    # logger.propagate = True # This is the default, no need to explicitly set unless you change it.

    # Add Discord handler if bot and channel ID are provided and it's not already added
    if bot and discord_log_channel_id:
        # Check if Discord handler already exists to prevent duplicates for named loggers
        if not any(isinstance(h, DiscordHandler) for h in logger.handlers):
            try:
                discord_handler = DiscordHandler(bot, discord_log_channel_id)
                discord_handler.setFormatter(LOGGING_FORMATTER)
                discord_handler.setLevel(logging.ERROR) # Only send ERROR and critical logs to Discord
                logger.addHandler(discord_handler)
            except Exception as e:
                # Log this failure to the console/file since Discord handler might not be ready
                root_logger.error(f"Failed to add Discord handler to logger '{name}': {e}")

    return logger

# Custom Discord Handler for sending logs to a Discord channel
class DiscordHandler(logging.Handler):
    def __init__(self, bot, channel_id):
        super().__init__()
        self.bot = bot
        self.channel_id = channel_id
        # Level set here is a default; it can be overridden when adding to a logger.
        # It's good practice to set it low and then set it higher when adding the handler.
        self.setLevel(logging.DEBUG)

    def emit(self, record):
        log_entry = self.format(record)
        # Check if bot is ready before attempting to send messages
        if self.bot and self.bot.is_ready():
            channel = self.bot.get_channel(self.channel_id)
            if channel:
                # Use asyncio.run_coroutine_threadsafe to run async send_message from a sync context
                coro = channel.send(f"```\n{log_entry}\n```")
                fut = asyncio.run_coroutine_threadsafe(coro, self.bot.loop)
                try:
                    fut.result(timeout=5) # Wait for the send operation to complete (with a timeout)
                except Exception as e:
                    # If sending to Discord fails, print to stderr as a fallback
                    print(f"Failed to send log to Discord channel {self.channel_id}: {e}", file=sys.stderr)
            else:
                print(f"Discord channel {self.channel_id} not found for logging.", file=sys.stderr)
        else:
            print("Discord bot not ready for logging. Log not sent to Discord.", file=sys.stderr)
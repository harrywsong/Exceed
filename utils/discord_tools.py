import discord
import utils.config as config

async def send_guild_log(bot, guild: discord.Guild, level: str, message: str):
    """
    Sends a log message to the configured log channel for a specific guild.

    Args:
        bot: The Discord bot instance.
        guild: The discord.Guild object for the server.
        level: The logging level (e.g., "INFO", "WARNING", "ERROR").
        message: The message content.
    """
    channel_id = config.get_channel_id(guild.id, 'log_channel')
    if channel_id:
        channel = bot.get_channel(channel_id)
        if isinstance(channel, discord.TextChannel):
            try:
                # Format the message to match your logger's style
                formatted_message = f"[{level.upper()}] {message}"
                await channel.send(f"```{formatted_message}```")
            except discord.Forbidden:
                print(f"Error: Bot lacks permissions to send logs to channel {channel.name} in guild {guild.name}.")
            except Exception as e:
                print(f"Failed to send log to Discord channel {channel_id}: {e}")
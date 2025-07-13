import discord
from discord.ext import commands
from discord import app_commands
import traceback # Import traceback for detailed error information

# Assuming utils.logger and config are correctly defined and accessible
from utils.logger import get_logger
from utils import config # Ensure config has LOG_CHANNEL_ID if it's used by get_logger

class ClearMessages(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        # Initialize the logger for this cog with its specific name.
        self.logger = get_logger(
            "메시지 정리",  # Message Cleanup
            bot=self.bot,
            discord_log_channel_id=config.LOG_CHANNEL_ID
        )
        self.logger.info("ClearMessages cog initialized with logger 'clearmessages'.")


    @app_commands.command(name="삭제", description="이 채널에서 최근 메시지를 삭제합니다.")
    @app_commands.describe(amount="삭제할 메시지 수 (최대 100개)")
    async def clear(self, interaction: discord.Interaction, amount: int):
        # Permission check: Ensure the user has 'manage_messages' permission in the channel
        if not interaction.channel.permissions_for(interaction.user).manage_messages:
            self.logger.info(
                f"Permission denied: {interaction.user.display_name} ({interaction.user.id}) "
                f"tried to use /삭제 in #{interaction.channel.name} ({interaction.channel.id})"
            )
            await interaction.response.send_message("❌ 이 명령어를 사용할 권한이 없습니다.", ephemeral=True)
            return

        # Input validation: Ensure the amount is within the valid range
        if amount < 1 or amount > 100:
            self.logger.info(
                f"Invalid amount: {interaction.user.display_name} ({interaction.user.id}) "
                f"tried to delete {amount} messages in #{interaction.channel.name} ({interaction.channel.id})"
            )
            await interaction.response.send_message("⚠️ 1에서 100 사이의 숫자를 입력해주세요.", ephemeral=True)
            return

        # Defer the response to avoid "The application did not respond"
        await interaction.response.defer(ephemeral=True)

        try:
            # Purge messages: amount + 1 to also delete the command message itself
            deleted = await interaction.channel.purge(limit=amount + 1)
            deleted_count = len(deleted) - 1 # Subtract 1 for the command message itself

            # Send a confirmation message to the user
            await interaction.followup.send(f"🧹 최근 메시지 {deleted_count}개를 삭제했습니다.", ephemeral=True)

            # Log the action
            self.logger.info(
                f"✅ {interaction.user.display_name} ({interaction.user.id}) "
                f"deleted {deleted_count} messages in #{interaction.channel.name} ({interaction.channel.id})."
            )

        except discord.Forbidden:
            # Handle cases where the bot doesn't have permissions to purge messages
            self.logger.error(
                f"❌ Bot lacks permissions to delete messages in #{interaction.channel.name} ({interaction.channel.id}): Forbidden.\n{traceback.format_exc()}"
            )
            await interaction.followup.send(
                "❌ 봇이 메시지를 삭제할 권한이 없습니다. 봇 역할의 권한을 확인해주세요.", ephemeral=True
            )
        except discord.HTTPException as e:
            # Handle other HTTP-related errors from Discord API
            self.logger.error(
                f"❌ HTTP error during message purge in #{interaction.channel.name} ({interaction.channel.id}): {e}\n{traceback.format_exc()}"
            )
            await interaction.followup.send(f"❌ 메시지 삭제 중 오류가 발생했습니다: `{e}`", ephemeral=True)
        except Exception as e:
            # Catch any other unexpected errors
            self.logger.critical(
                f"❌ Unknown error during message purge in #{interaction.channel.name} ({interaction.channel.id}): {e}\n{traceback.format_exc()}",
                exc_info=True
            )
            await interaction.followup.send("❌ 메시지 삭제 중 알 수 없는 오류가 발생했습니다.", ephemeral=True)


async def setup(bot):
    await bot.add_cog(ClearMessages(bot))
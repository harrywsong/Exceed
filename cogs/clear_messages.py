import discord
from discord.ext import commands
from discord import app_commands

class ClearMessages(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        # Access the bot's pre-configured logger
        self.logger = self.bot.logger if hasattr(self.bot, 'logger') else None
        if self.logger is None:
            # Fallback if for some reason the bot.logger isn't set, though it should be.
            from utils.logger import get_logger
            self.logger = get_logger("bot")
            self.logger.warning("Bot logger not found on bot instance in ClearMessages cog. Using fallback logger.")


    @app_commands.command(name="삭제", description="이 채널에서 최근 메시지를 삭제합니다.")
    @app_commands.describe(amount="삭제할 메시지 수 (최대 100개)")
    async def clear(self, interaction: discord.Interaction, amount: int):
        # Permission check: Ensure the user has 'manage_messages' permission in the channel
        if not interaction.channel.permissions_for(interaction.user).manage_messages:
            self.logger.info(f"Permission denied: {interaction.user} tried to use /삭제 in #{interaction.channel.name}")
            await interaction.response.send_message("❌ 이 명령어를 사용할 권한이 없습니다.", ephemeral=True)
            return

        # Input validation: Ensure the amount is within the valid range
        if amount < 1 or amount > 100:
            self.logger.info(f"Invalid amount: {interaction.user} tried to delete {amount} messages in #{interaction.channel.name}")
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
            if self.logger:
                self.logger.info(f"{interaction.user} ({interaction.user.id})가 #{interaction.channel.name} 채널에서 {deleted_count}개의 메시지를 삭제했습니다.")
            else:
                print(f"[{interaction.user}] deleted {deleted_count} messages in [{interaction.channel.name}] - Logger not initialized.")

        except discord.Forbidden:
            # Handle cases where the bot doesn't have permissions to purge messages
            self.logger.error(f"봇이 #{interaction.channel.name}에서 메시지를 삭제할 권한이 없습니다: Forbidden.")
            await interaction.followup.send("❌ 봇이 메시지를 삭제할 권한이 없습니다. 봇 역할의 권한을 확인해주세요.", ephemeral=True)
        except discord.HTTPException as e:
            # Handle other HTTP-related errors
            self.logger.error(f"메시지 삭제 중 HTTP 오류 발생 in #{interaction.channel.name}: {e}")
            await interaction.followup.send(f"❌ 메시지 삭제 중 오류가 발생했습니다: {e}", ephemeral=True)
        except Exception as e:
            # Catch any other unexpected errors
            self.logger.critical(f"알 수 없는 오류 발생 during message purge in #{interaction.channel.name}: {e}", exc_info=True)
            await interaction.followup.send("❌ 메시지 삭제 중 알 수 없는 오류가 발생했습니다.", ephemeral=True)


async def setup(bot):
    # Pass the bot instance directly to the cog.
    # The cog will then access bot.logger internally.
    await bot.add_cog(ClearMessages(bot))
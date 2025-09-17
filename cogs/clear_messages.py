# cogs/clear_messages.py - Updated for multi-server support
import discord
from discord.ext import commands
from discord import app_commands
import traceback

from utils.logger import get_logger
from utils.config import (
    is_feature_enabled,
    get_channel_id,
    is_server_configured
)


class ClearMessages(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        # FIX: The logger is now a global singleton, so we just get it by name.
        self.logger = get_logger("메시지 정리")
        self.logger.info("메시지 정리 기능이 초기화되었습니다.")

    @app_commands.command(name="삭제", description="이 채널에서 최근 메시지를 삭제합니다.")
    @app_commands.describe(amount="삭제할 메시지 수 (최대 100개)")
    async def clear(self, interaction: discord.Interaction, amount: int):
        # Check if server is configured
        if not interaction.guild or not is_server_configured(interaction.guild.id):
            await interaction.response.send_message("❌ 이 서버는 아직 설정되지 않았습니다. 관리자에게 문의하세요.", ephemeral=True)
            return

        # Check permissions
        if not interaction.channel.permissions_for(interaction.user).manage_messages:
            # FIX: Add guild_id to log message
            self.logger.info(
                f"Permission denied: {interaction.user.display_name} ({interaction.user.id}) "
                f"tried to use /삭제 in #{interaction.channel.name} ({interaction.channel.id})",
                extra={'guild_id': interaction.guild.id}
            )
            await interaction.response.send_message("❌ 이 명령어를 사용할 권한이 없습니다.", ephemeral=True)
            return

        # Validate amount
        if amount < 1 or amount > 100:
            # FIX: Add guild_id to log message
            self.logger.info(
                f"Invalid amount: {interaction.user.display_name} ({interaction.user.id}) "
                f"tried to delete {amount} messages in #{interaction.channel.name} ({interaction.channel.id})",
                extra={'guild_id': interaction.guild.id}
            )
            await interaction.response.send_message("⚠️ 1에서 100 사이의 숫자를 입력해주세요.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        try:
            deleted = await interaction.channel.purge(limit=amount + 1)
            deleted_count = len(deleted) - 1

            await interaction.followup.send(f"🧹 최근 메시지 {deleted_count}개를 삭제했습니다.", ephemeral=True)

            # Log to server-specific log channel
            # FIX: Add guild_id to log message
            self.logger.info(
                f"✅ {interaction.user.display_name} ({interaction.user.id}) "
                f"deleted {deleted_count} messages in #{interaction.channel.name} ({interaction.channel.id})",
                extra={'guild_id': interaction.guild.id}
            )

        except discord.Forbidden:
            # FIX: Add guild_id to log message
            self.logger.error(
                f"❌ Bot lacks permissions to delete messages in #{interaction.channel.name} ({interaction.channel.id}): Forbidden.\n{traceback.format_exc()}",
                extra={'guild_id': interaction.guild.id}
            )
            await interaction.followup.send(
                "❌ 봇이 메시지를 삭제할 권한이 없습니다. 봇 역할의 권한을 확인해주세요.", ephemeral=True
            )
        except discord.HTTPException as e:
            # FIX: Add guild_id to log message
            self.logger.error(
                f"❌ HTTP error during message purge in #{interaction.channel.name} ({interaction.channel.id}): {e}\n{traceback.format_exc()}",
                extra={'guild_id': interaction.guild.id}
            )
            await interaction.followup.send(f"❌ 메시지 삭제 중 오류가 발생했습니다: `{e}`", ephemeral=True)
        except Exception as e:
            # FIX: Add guild_id to log message
            self.logger.critical(
                f"❌ Unknown error during message purge in #{interaction.channel.name} ({interaction.channel.id}): {e}\n{traceback.format_exc()}",
                exc_info=True,
                extra={'guild_id': interaction.guild.id}
            )
            await interaction.followup.send("❌ 메시지 삭제 중 알 수 없는 오류가 발생했습니다.", ephemeral=True)


async def setup(bot):
    await bot.add_cog(ClearMessages(bot))
import discord
from discord.ext import commands
from discord import app_commands

class ClearMessages(commands.Cog):
    def __init__(self, bot, logger):
        self.bot = bot
        self.logger = logger

    @app_commands.command(name="삭제", description="이 채널에서 최근 메시지를 삭제합니다.")
    @app_commands.describe(amount="삭제할 메시지 수 (최대 100개)")
    async def clear(self, interaction: discord.Interaction, amount: int):
        if not interaction.channel.permissions_for(interaction.user).manage_messages:
            await interaction.response.send_message("❌ 이 명령어를 사용할 권한이 없습니다.", ephemeral=True)
            return

        if amount < 1 or amount > 100:
            await interaction.response.send_message("⚠️ 1에서 100 사이의 숫자를 입력해주세요.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        deleted = await interaction.channel.purge(limit=amount + 1)  # 명령어 메시지 포함

        deleted_count = len(deleted) - 1
        await interaction.followup.send(f"🧹 최근 메시지 {deleted_count}개를 삭제했습니다.", ephemeral=True)

        self.logger.info(f"{interaction.user}가 #{interaction.channel.name} 채널에서 {deleted_count}개의 메시지를 삭제했습니다.")

async def setup(bot):
    logger = getattr(bot, 'logger', None)
    await bot.add_cog(ClearMessages(bot, logger))

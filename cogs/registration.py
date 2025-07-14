import discord
from discord import app_commands
from discord.ext import commands
import utils.logger as logger_module
from utils import config

class Registration(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.logger = logger_module.get_logger(self.__class__.__name__)

    @app_commands.command(
        name="연동",
        description="디스코드 계정을 라이엇 ID와 연결합니다 (예: Name#Tag)."
    )
    @app_commands.describe(
        riot_id="라이엇 ID (예: winter#겨울밤)"
    )
    async def register(self, interaction: discord.Interaction, riot_id: str):
        await interaction.response.defer(ephemeral=True)

        if "#" not in riot_id:
            await interaction.followup.send(
                "❌ 올바르지 않은 형식입니다. `이름#태그` 형태로 입력해주세요.", ephemeral=True
            )
            self.log.warning(f"{interaction.user} failed to register with invalid Riot ID format: {riot_id}")
            return

        discord_id = interaction.user.id

        try:
            query = """
                INSERT INTO registrations (discord_id, riot_id)
                VALUES ($1, $2)
                ON CONFLICT (discord_id) DO UPDATE SET riot_id = EXCLUDED.riot_id
            """
            await self.bot.pool.execute(query, discord_id, riot_id)
            await interaction.followup.send(
                f"✅ 라이엇 ID `{riot_id}`와 성공적으로 연결되었습니다!", ephemeral=True
            )
            self.log.info(f"✅ {interaction.user} linked Riot ID: {riot_id}")
        except Exception as e:
            self.log.error(f"❌ Database error during registration for {interaction.user}: {e}")
            await interaction.followup.send(
                f"❌ 데이터베이스 오류가 발생했습니다: `{e}`", ephemeral=True
            )

    @app_commands.command(
        name="myriot",
        description="등록한 라이엇 ID를 확인합니다."
    )
    async def myriot(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        discord_id = interaction.user.id
        try:
            query = "SELECT riot_id FROM registrations WHERE discord_id = $1"
            row = await self.bot.pool.fetchrow(query, discord_id)
            if row and row["riot_id"]:
                await interaction.followup.send(
                    f"🔎 등록된 라이엇 ID: `{row['riot_id']}`", ephemeral=True
                )
                self.log.info(f"{interaction.user} checked Riot ID: {row['riot_id']}")
            else:
                await interaction.followup.send(
                    "아직 라이엇 ID를 등록하지 않았습니다. `/연동` 명령어로 등록해주세요.", ephemeral=True
                )
                self.log.info(f"{interaction.user} tried to check Riot ID but none was found.")
        except Exception as e:
            self.log.error(f"❌ Database error during myriot check for {interaction.user}: {e}")
            await interaction.followup.send(
                f"❌ 데이터베이스 오류가 발생했습니다: `{e}`", ephemeral=True
            )

async def setup(bot: commands.Bot):
    await bot.add_cog(Registration(bot))
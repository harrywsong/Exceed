import discord
from discord.ext import commands
import logging

logger = logging.getLogger("bot")

class AutoRoleCog(commands.Cog):
    def __init__(self, bot, role_ids: list[int]):  # accept a list of role IDs
        self.bot = bot
        self.role_ids = role_ids

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        roles_to_add = []
        for role_id in self.role_ids:
            role = member.guild.get_role(role_id)
            if role:
                roles_to_add.append(role)
        if roles_to_add:
            try:
                await member.add_roles(*roles_to_add, reason="회원 가입 시 자동 역할 부여")
                logger.info(f"{member.display_name}님에게 역할 {', '.join([r.name for r in roles_to_add])}을(를) 부여했습니다.")
            except discord.Forbidden:
                logger.error(f"{member.display_name}님에게 역할 부여 권한이 없습니다.")
            except Exception as e:
                logger.error(f"{member.display_name}님에게 역할 부여 중 오류 발생: {e}")

async def setup(bot):
    # 여기에 자동 부여할 역할 ID들을 리스트로 추가하세요
    ROLE_IDS = [
        1389711048461910057,
        1391814186912452741,
        1391812423966527498,
        1391812274087264329,
        1391812498549903421,
        1391812623816982668,
    ]
    await bot.add_cog(AutoRoleCog(bot, ROLE_IDS))

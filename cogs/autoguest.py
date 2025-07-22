import discord
from discord.ext import commands
import traceback

from utils.logger import get_logger
from utils import config

class AutoRoleCog(commands.Cog):
    def __init__(self, bot, role_ids: list[int]):
        self.bot = bot
        self.role_ids = role_ids
        self.logger = get_logger(
            "자동 역할 (게스트)",
            bot=self.bot,
            discord_log_channel_id=config.LOG_CHANNEL_ID
        )
        self.logger.info("자동 역할 기능이 초기화되었습니다.")

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        if member.bot:
            self.logger.debug(f"Ignored bot joining: {member.display_name} ({member.id}). No auto-roles applied.")
            return

        roles_to_add = []
        for role_id in self.role_ids:
            role = member.guild.get_role(role_id)
            if role:
                roles_to_add.append(role)
            else:
                self.logger.warning(f"Role with ID {role_id} not found in guild {member.guild.name} ({member.guild.id}) for auto-role.")

        if roles_to_add:
            try:
                await member.add_roles(*roles_to_add, reason="회원 가입 시 자동 역할 부여")
                self.logger.info(
                    f"✅ {member.display_name} ({member.id})님에게 역할 '{', '.join([r.name for r in roles_to_add])}'을(를) 부여했습니다."
                )
            except discord.Forbidden:
                self.logger.error(
                    f"❌ {member.display_name} ({member.id})님에게 역할 부여 권한이 없습니다. 봇 역할의 권한을 확인해주세요.\n"
                    f"{traceback.format_exc()}"
                )
                if hasattr(self.bot, 'get_channel') and config.LOG_CHANNEL_ID:
                    log_channel = self.bot.get_channel(config.LOG_CHANNEL_ID)
                    if log_channel:
                        await log_channel.send(
                            f"🚨 **AutoRole 오류:** `{member.display_name}` ({member.id})님에게 역할 부여 실패: `권한 부족`\n"
                            f"봇 역할의 권한을 확인해주세요."
                        )
            except Exception as e:
                self.logger.error(
                    f"❌ {member.display_name} ({member.id})님에게 역할 부여 중 알 수 없는 오류 발생: {e}\n"
                    f"{traceback.format_exc()}"
                )
                if hasattr(self.bot, 'get_channel') and config.LOG_CHANNEL_ID:
                    log_channel = self.bot.get_channel(config.LOG_CHANNEL_ID)
                    if log_channel:
                        await log_channel.send(
                            f"🚨 **AutoRole 오류:** `{member.display_name}` ({member.id})님에게 역할 부여 중 예상치 못한 오류: `{e}`"
                        )
        else:
            self.logger.info(
                f"🤔 {member.display_name} ({member.id})님에게 부여할 자동 역할이 없습니다. 설정된 역할 ID들을 확인해주세요."
            )


async def setup(bot):
    await bot.add_cog(AutoRoleCog(bot, config.AUTO_ROLE_IDS))
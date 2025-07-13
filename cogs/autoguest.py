import discord
from discord.ext import commands
import traceback # Import traceback for detailed error logging

# Import the get_logger function and config
from utils.logger import get_logger
from utils import config # Assuming config has LOG_CHANNEL_ID if get_logger uses it

class AutoRoleCog(commands.Cog):
    def __init__(self, bot, role_ids: list[int]):  # accept a list of role IDs
        self.bot = bot
        self.role_ids = role_ids
        # Directly get a named logger for this cog.
        # This is the recommended and most robust approach.
        self.logger = get_logger(
            "자동 역할 (게스트)",  # A specific and descriptive name for this cog's logger
            bot=self.bot, # Pass the bot instance to ensure DiscordLogHandler is attached if configured
            discord_log_channel_id=config.LOG_CHANNEL_ID # Pass the log channel ID
        )
        self.logger.info("AutoRoleCog initialized.")


    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        # Ignore bots joining to prevent unwanted role assignments or errors
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
                # Log the names of the roles added for clarity
                self.logger.info(
                    f"✅ {member.display_name} ({member.id})님에게 역할 '{', '.join([r.name for r in roles_to_add])}'을(를) 부여했습니다."
                )
            except discord.Forbidden:
                # Log the full traceback for Forbidden errors, as it's critical
                self.logger.error(
                    f"❌ {member.display_name} ({member.id})님에게 역할 부여 권한이 없습니다. 봇 역할의 권한을 확인해주세요.\n"
                    f"{traceback.format_exc()}"
                )
                # Optionally, notify a specific log channel in Discord for critical errors
                if hasattr(self.bot, 'get_channel') and config.LOG_CHANNEL_ID:
                    log_channel = self.bot.get_channel(config.LOG_CHANNEL_ID)
                    if log_channel:
                        await log_channel.send(
                            f"🚨 **AutoRole 오류:** `{member.display_name}` ({member.id})님에게 역할 부여 실패: `권한 부족`\n"
                            f"봇 역할의 권한을 확인해주세요."
                        )
            except Exception as e:
                # Log the full traceback for any other unexpected errors
                self.logger.error(
                    f"❌ {member.display_name} ({member.id})님에게 역할 부여 중 알 수 없는 오류 발생: {e}\n"
                    f"{traceback.format_exc()}"
                )
                # Optionally, notify a specific log channel in Discord for critical errors
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
    # Here, you define the role IDs that should be automatically assigned.
    # Ensure these IDs are correct for your Discord server.
    # For example, you might want a "New Member" or "Guest" role.
    # It's highly recommended to store these in your config.py for easier management.
    ROLE_IDS = [
        1389711048461910057, # Example Role 1
        1391814186912452741, # Example Role 2
        1391812423966527498, # Example Role 3
        1391812274087264329, # Example Role 4
        1391812498549903421, # Example Role 5
        1391812623816982668, # Example Role 6
    ]
    # If these roles are dynamic or specific to your config, consider importing them from utils.config
    # Example: ROLE_IDS = config.DEFAULT_MEMBER_ROLES

    await bot.add_cog(AutoRoleCog(bot, ROLE_IDS))
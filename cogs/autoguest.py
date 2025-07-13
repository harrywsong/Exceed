import discord
from discord.ext import commands
import traceback # Import traceback for detailed error logging

# We no longer explicitly define `logger = logging.getLogger("bot")` here
# as we will get the logger instance directly from the bot.

class AutoRoleCog(commands.Cog):
    def __init__(self, bot, role_ids: list[int]):  # accept a list of role IDs
        self.bot = bot
        self.role_ids = role_ids
        # Access the bot's pre-configured logger
        # This assumes self.bot.logger is set up in your main.py
        self.logger = self.bot.logger if hasattr(self.bot, 'logger') else None
        if self.logger is None:
            # Fallback if for some reason the bot.logger isn't set.
            # This is a safeguard and should ideally not be triggered.
            from utils.logger import get_logger
            self.logger = get_logger("auto_role_fallback") # Use a specific name for the fallback logger
            self.logger.warning("Bot logger not found on bot instance in AutoRoleCog. Using fallback logger.")


    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        # Ignore bots joining to prevent unwanted role assignments or errors
        if member.bot:
            return

        roles_to_add = []
        for role_id in self.role_ids:
            role = member.guild.get_role(role_id)
            if role:
                roles_to_add.append(role)
            else:
                if self.logger:
                    self.logger.warning(f"Role with ID {role_id} not found in guild {member.guild.name} ({member.guild.id}) for auto-role.")

        if roles_to_add:
            try:
                await member.add_roles(*roles_to_add, reason="회원 가입 시 자동 역할 부여")
                if self.logger:
                    self.logger.info(f"✅ {member.display_name} ({member.id})님에게 역할 {', '.join([r.name for r in roles_to_add])}을(를) 부여했습니다.")
            except discord.Forbidden:
                if self.logger:
                    self.logger.error(f"❌ {member.display_name} ({member.id})님에게 역할 부여 권한이 없습니다. 봇 역할의 권한을 확인해주세요.\n{traceback.format_exc()}")
                else:
                    print(f"ERROR: No permission to add roles to {member.display_name}.")
            except Exception as e:
                if self.logger:
                    self.logger.error(f"❌ {member.display_name} ({member.id})님에게 역할 부여 중 오류 발생: {e}\n{traceback.format_exc()}")
                else:
                    print(f"ERROR: Failed to add roles to {member.display_name}: {e}")
        else:
            if self.logger:
                self.logger.info(f"🤔 {member.display_name} ({member.id})님에게 부여할 자동 역할이 없습니다. 설정된 역할 ID들을 확인해주세요.")


async def setup(bot):
    # Here, you define the role IDs that should be automatically assigned.
    # Ensure these IDs are correct for your Discord server.
    # For example, you might want a "New Member" or "Guest" role.
    ROLE_IDS = [
        1389711048461910057,
        1391814186912452741,
        1391812423966527498,
        1391812274087264329,
        1391812498549903421,
        1391812623816982668,
    ]
    # Pass the bot instance and the list of role IDs to the cog.
    await bot.add_cog(AutoRoleCog(bot, ROLE_IDS))
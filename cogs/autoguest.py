# cogs/autoguest.py - Updated for multi-server support
import discord
from discord.ext import commands
import traceback

from utils.logger import get_logger
from utils.config import (
    get_channel_id,
    get_role_id,
    is_feature_enabled,
    is_server_configured,
    get_all_server_configs
)


class AutoRoleCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.logger = get_logger("자동 역할 (게스트)", bot=self.bot)
        self.logger.info("자동 역할 기능이 초기화되었습니다.")

    def get_auto_roles_for_server(self, guild_id: int) -> list[int]:
        """Get auto roles for a specific server"""
        # Load server config to get auto roles
        try:
            all_configs = get_all_server_configs()
            server_config = all_configs.get(str(guild_id), {})

            auto_roles = []

            # Add configured auto roles from server settings
            auto_role_ids = server_config.get('auto_role_ids', [])
            if isinstance(auto_role_ids, list):
                auto_roles.extend(auto_role_ids)

            # Always add unverified role if configured
            unverified_role_id = get_role_id(guild_id, 'unverified_role')
            if unverified_role_id and unverified_role_id not in auto_roles:
                auto_roles.append(unverified_role_id)

            return auto_roles

        except Exception as e:
            self.logger.error(f"Error getting auto roles for guild {guild_id}: {e}")
            return []

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        if member.bot:
            self.logger.debug(
                f"Ignored bot joining: {member.display_name} ({member.id}) in guild {member.guild.id}. No auto-roles applied.")
            return

        # Check if server is configured
        if not is_server_configured(member.guild.id):
            self.logger.debug(f"Guild {member.guild.id} not configured. Skipping auto-role for {member.display_name}")
            return

        # Check if welcome messages feature is enabled (we use this as a proxy for auto-role being enabled)
        if not is_feature_enabled(member.guild.id, 'welcome_messages'):
            self.logger.debug(
                f"Welcome messages (auto-role) disabled for guild {member.guild.id}. Skipping auto-role for {member.display_name}")
            return

        # Get log channel for this server
        log_channel_id = get_channel_id(member.guild.id, 'log_channel')
        log_channel = self.bot.get_channel(log_channel_id) if log_channel_id else None

        # Get auto roles for this server
        role_ids = self.get_auto_roles_for_server(member.guild.id)

        if not role_ids:
            self.logger.info(
                f"No auto roles configured for guild {member.guild.id}. Skipping auto-role for {member.display_name}")
            return

        roles_to_add = []
        for role_id in role_ids:
            role = member.guild.get_role(role_id)
            if role:
                roles_to_add.append(role)
            else:
                self.logger.warning(
                    f"Role with ID {role_id} not found in guild {member.guild.name} ({member.guild.id}) for auto-role."
                )

        if roles_to_add:
            try:
                await member.add_roles(*roles_to_add, reason="회원 가입 시 자동 역할 부여")
                self.logger.info(
                    f"✅ {member.display_name} ({member.id})님에게 역할 '{', '.join([r.name for r in roles_to_add])}'을(를) 부여했습니다. (Guild: {member.guild.id})"
                )

                # Log to server's log channel if available
                if log_channel:
                    try:
                        embed = discord.Embed(
                            title="🎭 자동 역할 부여",
                            description=f"{member.mention} ({member.display_name})님에게 자동 역할이 부여되었습니다.",
                            color=discord.Color.green()
                        )
                        embed.add_field(
                            name="부여된 역할",
                            value=", ".join([role.mention for role in roles_to_add]),
                            inline=False
                        )
                        embed.set_thumbnail(url=member.display_avatar.url)
                        await log_channel.send(embed=embed)
                    except discord.Forbidden:
                        self.logger.warning(f"No permission to send to log channel in guild {member.guild.id}")

            except discord.Forbidden:
                error_msg = (
                    f"❌ {member.display_name} ({member.id})님에게 역할 부여 권한이 없습니다. "
                    f"봇 역할의 권한을 확인해주세요. (Guild: {member.guild.id})"
                )
                self.logger.error(f"{error_msg}\n{traceback.format_exc()}")

                if log_channel:
                    try:
                        await log_channel.send(
                            f"🚨 **AutoRole 오류:** `{member.display_name}` ({member.id})님에게 역할 부여 실패: `권한 부족`\n"
                            f"봇 역할의 권한을 확인해주세요."
                        )
                    except discord.Forbidden:
                        pass

            except Exception as e:
                error_msg = (
                    f"❌ {member.display_name} ({member.id})님에게 역할 부여 중 알 수 없는 오류 발생: {e} "
                    f"(Guild: {member.guild.id})"
                )
                self.logger.error(f"{error_msg}\n{traceback.format_exc()}")

                if log_channel:
                    try:
                        await log_channel.send(
                            f"🚨 **AutoRole 오류:** `{member.display_name}` ({member.id})님에게 역할 부여 중 예상치 못한 오류: `{e}`"
                        )
                    except discord.Forbidden:
                        pass
        else:
            self.logger.info(
                f"🤔 {member.display_name} ({member.id})님에게 부여할 자동 역할이 없습니다. "
                f"Guild {member.guild.id}의 설정된 역할 ID들을 확인해주세요."
            )

    @commands.Cog.listener()
    async def on_guild_join(self, guild):
        """Log when bot joins a new guild"""
        self.logger.info(f"Bot joined new guild: {guild.name} ({guild.id}) - Members: {guild.member_count}")

    @commands.Cog.listener()
    async def on_guild_remove(self, guild):
        """Log when bot leaves a guild"""
        self.logger.info(f"Bot left guild: {guild.name} ({guild.id})")


async def setup(bot):
    await bot.add_cog(AutoRoleCog(bot))
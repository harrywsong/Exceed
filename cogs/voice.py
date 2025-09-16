# cogs/voice.py - Updated for multi-server support
import discord
from discord.ext import commands, tasks
import traceback

from utils.config import (
    get_channel_id,
    get_role_id,
    is_feature_enabled,
    get_server_setting,
    is_server_configured
)
from utils.logger import get_logger


class TempVoice(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.logger = get_logger(
            "임시 음성",
            bot=self.bot,
            discord_log_channel_id=0  # Will be set per guild
        )

        # Per-guild temp channels tracking
        self.temp_channels = {}  # guild_id: {channel_id: owner_id}

        self.cleanup_empty_channels.start()
        self.logger.info("임시 음성 채널 기능이 초기화되었습니다.")

    def cog_unload(self):
        self.cleanup_empty_channels.cancel()
        self.logger.info("TempVoice Cog 언로드됨, 정리 작업 취소.")

    @tasks.loop(minutes=10)
    async def cleanup_empty_channels(self):
        await self.bot.wait_until_ready()

        for guild in self.bot.guilds:
            if not is_server_configured(guild.id) or not is_feature_enabled(guild.id, 'voice_channels'):
                continue

            category_id = get_channel_id(guild.id, 'temp_voice_category')
            if not category_id:
                continue

            category = guild.get_channel(category_id)
            if not category or not isinstance(category, discord.CategoryChannel):
                self.logger.warning(f"❌ 길드 {guild.id}의 카테고리 채널 ID {category_id}을(를) 찾을 수 없거나 정리 작업에 적합하지 않습니다.")
                continue

            lobby_channel_id = get_channel_id(guild.id, 'lobby_voice')
            guild_temp_channels = self.temp_channels.get(guild.id, {})

            for channel in list(category.voice_channels):
                if channel.id == lobby_channel_id:
                    continue

                if len(channel.members) == 0:
                    try:
                        await channel.delete()
                        if guild.id in self.temp_channels and channel.id in self.temp_channels[guild.id]:
                            del self.temp_channels[guild.id][channel.id]
                        self.logger.info(f"🗑️ 길드 {guild.name}에서 비어 있는 음성 채널 삭제됨: '{channel.name}' (ID: {channel.id})")
                    except discord.Forbidden:
                        self.logger.error(f"❌ 길드 {guild.name}에서 채널 {channel.name} ({channel.id}) 삭제 권한이 없습니다.")
                    except Exception as e:
                        self.logger.error(
                            f"❌ 길드 {guild.name}에서 채널 '{channel.name}' ({channel.id}) 삭제 실패: {e}\n{traceback.format_exc()}")
                else:
                    self.logger.debug(f"길드 {guild.name}의 음성 채널 '{channel.name}' (ID: {channel.id})에 멤버가 있어 삭제하지 않습니다.")

    @cleanup_empty_channels.before_loop
    async def before_cleanup(self):
        self.logger.info("정리 작업 시작 전 봇 준비 대기 중...")
        await self.bot.wait_until_ready()
        self.logger.info("정리 작업 시작 전 봇 준비 완료.")

    @commands.Cog.listener()
    async def on_voice_state_update(self, member: discord.Member, before: discord.VoiceState,
                                    after: discord.VoiceState):
        if member.bot:
            return

        guild_id = member.guild.id

        # Check if server is configured and feature is enabled
        if not is_server_configured(guild_id) or not is_feature_enabled(guild_id, 'voice_channels'):
            return

        lobby_channel_id = get_channel_id(guild_id, 'lobby_voice')
        category_id = get_channel_id(guild_id, 'temp_voice_category')

        if not lobby_channel_id or not category_id:
            return

        # Handle joining lobby channel
        if after.channel and after.channel.id == lobby_channel_id:
            category = member.guild.get_channel(category_id)
            if not category or not isinstance(category, discord.CategoryChannel):
                self.logger.warning(f"❌ 길드 {guild_id}의 카테고리 채널 ID {category_id}을(를) 찾을 수 없거나 유효하지 않습니다!")
                try:
                    await member.send("죄송합니다, 임시 채널을 생성할 수 없습니다. 관리자에게 문의해주세요.")
                except discord.Forbidden:
                    pass
                return

            try:
                guild = member.guild

                # Get member role from server config
                member_role_id = get_role_id(guild_id, 'member_role')

                overwrites = {
                    guild.default_role: discord.PermissionOverwrite(connect=False),
                    member: discord.PermissionOverwrite(
                        connect=True,
                        view_channel=True,
                        manage_channels=True,
                        move_members=True,
                        mute_members=True,
                        deafen_members=True,
                        speak=True,
                        stream=True
                    ),
                }

                # Add member role permissions if configured
                if member_role_id:
                    allowed_role = guild.get_role(member_role_id)
                    if allowed_role:
                        overwrites[allowed_role] = discord.PermissionOverwrite(
                            connect=True,
                            view_channel=True
                        )

                # Get custom channel name format from server settings
                channel_name_format = get_server_setting(guild_id, 'temp_channel_name_format', '〔🔊〕{username}님의 음성채널')
                channel_name = channel_name_format.format(username=member.display_name)

                # Get user limit from server settings
                user_limit = get_server_setting(guild_id, 'temp_channel_user_limit', None)

                new_channel = await category.create_voice_channel(
                    name=channel_name,
                    overwrites=overwrites,
                    user_limit=user_limit
                )

                # Track the temp channel
                if guild_id not in self.temp_channels:
                    self.temp_channels[guild_id] = {}
                self.temp_channels[guild_id][new_channel.id] = member.id

                await member.move_to(new_channel)

                self.logger.info(
                    f"➕ 길드 {guild.name}에서 사용자 {member.display_name} ({member.id})님을 위해 임시 음성 채널 '{new_channel.name}' (ID: {new_channel.id})을(를) 생성하고 이동시켰습니다.")
            except discord.Forbidden:
                self.logger.error(
                    f"❌ 길드 {guild.name}에서 {member.display_name}님을 위한 임시 음성 채널 생성 또는 이동 권한이 없습니다.")
                try:
                    await member.send("죄송합니다, 임시 채널을 생성하거나 이동할 권한이 없습니다. 봇 권한을 확인해주세요.")
                except discord.Forbidden:
                    pass
            except Exception as e:
                self.logger.error(
                    f"❌ 길드 {guild.name}에서 {member.display_name}님을 위한 임시 음성 채널 생성 또는 이동 실패: {e}\n{traceback.format_exc()}")
                try:
                    await member.send("죄송합니다, 임시 채널 생성 중 알 수 없는 오류가 발생했습니다. 관리자에게 문의해주세요.")
                except discord.Forbidden:
                    pass

        # Handle leaving temp channels
        if before.channel and guild_id in self.temp_channels and before.channel.id in self.temp_channels[guild_id]:
            if len(before.channel.members) == 0:
                try:
                    await before.channel.delete()
                    del self.temp_channels[guild_id][before.channel.id]
                    self.logger.info(
                        f"🗑️ 길드 {member.guild.name}에서 빈 임시 음성 채널 삭제됨: '{before.channel.name}' (ID: {before.channel.id})")
                except discord.Forbidden:
                    self.logger.error(
                        f"❌ 길드 {member.guild.name}에서 빈 임시 채널 {before.channel.name} ({before.channel.id}) 삭제 권한이 없습니다.")
                except Exception as e:
                    self.logger.error(
                        f"❌ 길드 {member.guild.name}에서 빈 임시 채널 '{before.channel.name}' ({before.channel.id}) 삭제 실패: {e}\n{traceback.format_exc()}")
            else:
                self.logger.debug(
                    f"길드 {member.guild.name}의 음성 채널 '{before.channel.name}' (ID: {before.channel.id})에 아직 멤버가 있어 삭제하지 않습니다.")

    @commands.Cog.listener()
    async def on_guild_join(self, guild):
        """Handle bot joining a new guild"""
        self.logger.info(f"Bot joined new guild for voice: {guild.name} ({guild.id})")

    @commands.Cog.listener()
    async def on_guild_remove(self, guild):
        """Handle bot leaving a guild"""
        self.logger.info(f"Bot left guild for voice: {guild.name} ({guild.id})")
        # Clean up temp channels tracking
        if guild.id in self.temp_channels:
            del self.temp_channels[guild.id]


async def setup(bot):
    await bot.add_cog(TempVoice(bot))
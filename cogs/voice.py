import discord
from discord.ext import commands, tasks
import traceback
import asyncio # Make sure asyncio is imported for sleep

from utils import config
from utils.logger import get_logger


class TempVoice(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.lobby_channel_id = config.LOBBY_VOICE_CHANNEL_ID
        self.category_id = config.TEMP_VOICE_CATEGORY_ID
        self.temp_channels = {}

        # Updated: Directly get the logger with the desired Korean name
        self.logger = get_logger(
            "임시 음성", # Korean for "Temporary Voice"
            bot=self.bot,
            discord_log_channel_id=config.LOG_CHANNEL_ID
        )

        # Start the cleanup task loop
        self.cleanup_empty_channels.start()
        self.logger.info("TempVoice Cog 초기화 완료.")

    def cog_unload(self):
        # Cancel the cleanup task when cog unloads
        self.cleanup_empty_channels.cancel()
        self.logger.info("TempVoice Cog 언로드됨, 정리 작업 취소.")

    @tasks.loop(minutes=10)
    async def cleanup_empty_channels(self):
        await self.bot.wait_until_ready() # Ensure bot is ready before doing Discord operations

        category = self.bot.get_channel(self.category_id)
        if not category or not isinstance(category, discord.CategoryChannel):
            self.logger.warning(f"❌ 카테고리 채널 ID {self.category_id}을(를) 찾을 수 없거나 정리 작업에 적합하지 않습니다. (TempVoice)")
            return

        # Use list() to iterate over a copy, preventing issues if channels are deleted during iteration
        for channel in list(category.voice_channels):
            # Skip the lobby channel itself
            if channel.id == self.lobby_channel_id:
                continue

            # Check if the channel is empty
            if len(channel.members) == 0:
                try:
                    await channel.delete()
                    self.temp_channels.pop(channel.id, None) # Remove from tracked temp channels
                    self.logger.info(f"🗑️ 비어 있는 음성 채널 삭제됨: '{channel.name}' (ID: {channel.id})")
                except discord.Forbidden:
                    self.logger.error(f"❌ 채널 {channel.name} ({channel.id}) 삭제 권한이 없습니다. 봇 권한을 확인해주세요.")
                except Exception as e:
                    self.logger.error(f"❌ 채널 '{channel.name}' ({channel.id}) 삭제 실패: {e}\n{traceback.format_exc()}")
            else:
                self.logger.debug(f"음성 채널 '{channel.name}' (ID: {channel.id})에 멤버가 있어 삭제하지 않습니다.")

    @cleanup_empty_channels.before_loop
    async def before_cleanup(self):
        self.logger.info("정리 작업 시작 전 봇 준비 대기 중...")
        await self.bot.wait_until_ready()
        self.logger.info("정리 작업 시작 전 봇 준비 완료.")


    @commands.Cog.listener()
    async def on_voice_state_update(self, member: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
        # Ignore bots
        if member.bot:
            return

        # User joins the lobby channel - create temp channel and move user
        if after.channel and after.channel.id == self.lobby_channel_id:
            category = self.bot.get_channel(self.category_id)
            if not category or not isinstance(category, discord.CategoryChannel):
                self.logger.warning(f"❌ 카테고리 채널 ID {self.category_id}을(를) 찾을 수 없거나 유효하지 않습니다! (TempVoice)")
                if member.voice.channel == after.channel: # Only send if they are still in the lobby
                    try:
                        await member.send("죄송합니다, 임시 채널을 생성할 수 없습니다. 관리자에게 문의해주세요.")
                    except discord.Forbidden:
                        self.logger.warning(f"Cannot send DM to {member.display_name} regarding temp channel creation failure.")
                return

            try:
                guild = member.guild

                # Define overwrites for the new channel
                overwrites = {
                    guild.default_role: discord.PermissionOverwrite(connect=False), # Default role cannot connect
                    member: discord.PermissionOverwrite( # User who creates can connect, manage etc.
                        connect=True,
                        view_channel=True,
                        manage_channels=True, # Allow user to manage their own channel (name, user limit)
                        move_members=True,    # Allow user to move other members
                        mute_members=True,    # Allow user to mute/unmute members
                        deafen_members=True,  # Allow user to deafen/undeafen members
                        speak=True,
                        stream=True # Allow streaming
                    ),
                    # Bot's own permissions should be set at the category level or guild level
                    # If the bot needs to always see and manage, ensure it has `manage_channels` in the category/guild
                }

                # Create the new voice channel
                new_channel = await category.create_voice_channel(
                    name=f"🎙️・{member.display_name}님의 채널", # Dynamic name
                    overwrites=overwrites,
                    user_limit=None # No user limit by default, can be changed by manager
                )
                self.temp_channels[new_channel.id] = member.id # Track who owns the channel

                # Move the user to the new channel
                await member.move_to(new_channel)

                self.logger.info(f"➕ 사용자 {member.display_name} ({member.id})님을 위해 임시 음성 채널 '{new_channel.name}' (ID: {new_channel.id})을(를) 생성하고 이동시켰습니다.")
            except discord.Forbidden:
                self.logger.error(f"❌ {member.display_name}님을 위한 임시 음성 채널 생성 또는 이동 권한이 없습니다. 봇 권한을 확인해주세요.\n{traceback.format_exc()}")
                try:
                    await member.send("죄송합니다, 임시 채널을 생성하거나 이동할 권한이 없습니다. 봇 권한을 확인해주세요.")
                except discord.Forbidden: pass # Ignore if DM fails
            except Exception as e:
                self.logger.error(f"❌ {member.display_name}님을 위한 임시 음성 채널 생성 또는 이동 실패: {e}\n{traceback.format_exc()}")
                try:
                    await member.send("죄송합니다, 임시 채널 생성 중 알 수 없는 오류가 발생했습니다. 관리자에게 문의해주세요.")
                except discord.Forbidden: pass # Ignore if DM fails

        # User leaves a voice channel - delete temp channel if empty
        # Check if they left a channel that was one of our tracked temp channels
        if before.channel and before.channel.id in self.temp_channels:
            # Check if the channel is now empty
            if len(before.channel.members) == 0:
                try:
                    await before.channel.delete()
                    self.temp_channels.pop(before.channel.id, None) # Remove from tracking
                    self.logger.info(f"🗑️ 빈 임시 음성 채널 삭제됨: '{before.channel.name}' (ID: {before.channel.id})")
                except discord.Forbidden:
                    self.logger.error(f"❌ 빈 임시 채널 {before.channel.name} ({before.channel.id}) 삭제 권한이 없습니다. 봇 권한을 확인해주세요.")
                except Exception as e:
                    self.logger.error(f"❌ 빈 임시 채널 '{before.channel.name}' ({before.channel.id}) 삭제 실패: {e}\n{traceback.format_exc()}")
            else:
                self.logger.debug(f"음성 채널 '{before.channel.name}' (ID: {before.channel.id})에 아직 멤버가 있어 삭제하지 않습니다.")


async def setup(bot):
    await bot.add_cog(TempVoice(bot))
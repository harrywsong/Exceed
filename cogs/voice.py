import discord
from discord.ext import commands
import logging
from utils import config

logger = logging.getLogger("bot")

class TempVoice(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.lobby_channel_id = config.LOBBY_VOICE_CHANNEL_ID
        self.category_id = config.TEMP_VOICE_CATEGORY_ID
        self.temp_channels = {}

        # Start cleanup task after bot is ready
        self.cleanup_task = self.bot.loop.create_task(self.cleanup_empty_channels())

    async def cleanup_empty_channels(self):
        await self.bot.wait_until_ready()

        category = self.bot.get_channel(self.category_id)
        if not category or not isinstance(category, discord.CategoryChannel):
            logger.warning("카테고리 채널을 찾을 수 없거나 정리 작업에 적합하지 않습니다!")
            return

        for channel in category.voice_channels:
            if channel.id == self.lobby_channel_id:
                continue
            if len(channel.members) == 0:
                try:
                    await channel.delete()
                    logger.info(f"비어 있는 음성 채널 삭제됨: {channel.name}")
                except Exception as e:
                    logger.error(f"채널 {channel.name} 삭제 실패: {e}")

    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):
        # User joins the lobby channel - create temp channel and move user
        if after.channel and after.channel.id == self.lobby_channel_id:
            category = self.bot.get_channel(self.category_id)
            if not category or not isinstance(category, discord.CategoryChannel):
                logger.warning("카테고리 채널을 찾을 수 없거나 유효하지 않습니다!")
                return

            try:
                guild = member.guild

                overwrites = {
                    guild.default_role: discord.PermissionOverwrite(connect=True, view_channel=True),
                    member: discord.PermissionOverwrite(
                        manage_channels=True,
                        move_members=True,
                        mute_members=True,
                        deafen_members=True,
                        connect=True,
                        speak=True
                    )
                }

                new_channel = await category.create_voice_channel(
                    name=f"🎙️・{member.display_name}님의 채널",
                    overwrites=overwrites
                )
                self.temp_channels[new_channel.id] = member.id
                await member.move_to(new_channel)

                logger.info(f"사용자 {member.display_name}님을 위해 임시 음성 채널 '{new_channel.name}'을(를) 생성하고 이동시켰습니다.")
            except Exception as e:
                logger.error(f"{member.display_name}님을 위한 임시 음성 채널 생성 또는 이동 실패: {e}")

        # User leaves a voice channel - delete temp channel if empty
        if before.channel and before.channel.id in self.temp_channels:
            if len(before.channel.members) == 0:
                try:
                    await before.channel.delete()
                    self.temp_channels.pop(before.channel.id, None)
                    logger.info(f"빈 임시 음성 채널 삭제됨: {before.channel.name}")
                except Exception as e:
                    logger.error(f"임시 채널 {before.channel.name} 삭제 실패: {e}")


async def setup(bot):
    await bot.add_cog(TempVoice(bot))

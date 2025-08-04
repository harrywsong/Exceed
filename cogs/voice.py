import discord
from discord.ext import commands, tasks
import traceback

from utils import config
from utils.logger import get_logger


class TempVoice(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.lobby_channel_id = config.LOBBY_VOICE_CHANNEL_ID
        self.category_id = config.TEMP_VOICE_CATEGORY_ID
        self.temp_channels = {}

        self.logger = get_logger(
            "임시 음성",
            bot=self.bot,
            discord_log_channel_id=config.LOG_CHANNEL_ID
        )

        self.cleanup_empty_channels.start()
        self.logger.info("임시 음성 채널 기능이 초기화되었습니다.")

    def cog_unload(self):
        self.cleanup_empty_channels.cancel()
        self.logger.info("TempVoice Cog 언로드됨, 정리 작업 취소.")

    @tasks.loop(minutes=10)
    async def cleanup_empty_channels(self):
        await self.bot.wait_until_ready()

        category = self.bot.get_channel(self.category_id)
        if not category or not isinstance(category, discord.CategoryChannel):
            self.logger.warning(f"❌ 카테고리 채널 ID {self.category_id}을(를) 찾을 수 없거나 정리 작업에 적합하지 않습니다. (TempVoice)")
            return

        for channel in list(category.voice_channels):
            if channel.id == self.lobby_channel_id:
                continue

            if len(channel.members) == 0:
                try:
                    await channel.delete()
                    self.temp_channels.pop(channel.id, None)
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
    async def on_voice_state_update(self, member: discord.Member, before: discord.VoiceState,
                                    after: discord.VoiceState):
        if member.bot:
            return

        if after.channel and after.channel.id == self.lobby_channel_id:
            category = self.bot.get_channel(self.category_id)
            if not category or not isinstance(category, discord.CategoryChannel):
                self.logger.warning(f"❌ 카테고리 채널 ID {self.category_id}을(를) 찾을 수 없거나 유효하지 않습니다! (TempVoice)")
                if member.voice.channel == after.channel:
                    try:
                        await member.send("죄송합니다, 임시 채널을 생성할 수 없습니다. 관리자에게 문의해주세요.")
                    except discord.Forbidden:
                        self.logger.warning(
                            f"Cannot send DM to {member.display_name} regarding temp channel creation failure.")
                return

            try:
                guild = member.guild

                #
                # <--- CHANGE THE ROLE ID ON THIS LINE
                #
                allowed_role = guild.get_role(1389711143756501012)

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

                # 만약 역할이 존재한다면 overwrites에 추가합니다.
                if allowed_role:
                    overwrites[allowed_role] = discord.PermissionOverwrite(
                        connect=True,
                        view_channel=True
                    )

                new_channel = await category.create_voice_channel(
                    name=f"🎙️・{member.display_name}님의 채널",
                    overwrites=overwrites,
                    user_limit=None
                )
                self.temp_channels[new_channel.id] = member.id

                await member.move_to(new_channel)

                self.logger.info(
                    f"➕ 사용자 {member.display_name} ({member.id})님을 위해 임시 음성 채널 '{new_channel.name}' (ID: {new_channel.id})을(를) 생성하고 이동시켰습니다.")
            except discord.Forbidden:
                self.logger.error(
                    f"❌ {member.display_name}님을 위한 임시 음성 채널 생성 또는 이동 권한이 없습니다. 봇 권한을 확인해주세요.\n{traceback.format_exc()}")
                try:
                    await member.send("죄송합니다, 임시 채널을 생성하거나 이동할 권한이 없습니다. 봇 권한을 확인해주세요.")
                except discord.Forbidden:
                    pass
            except Exception as e:
                self.logger.error(f"❌ {member.display_name}님을 위한 임시 음성 채널 생성 또는 이동 실패: {e}\n{traceback.format_exc()}")
                try:
                    await member.send("죄송합니다, 임시 채널 생성 중 알 수 없는 오류가 발생했습니다. 관리자에게 문의해주세요.")
                except discord.Forbidden:
                    pass

        if before.channel and before.channel.id in self.temp_channels:
            if len(before.channel.members) == 0:
                try:
                    await before.channel.delete()
                    self.temp_channels.pop(before.channel.id, None)
                    self.logger.info(f"🗑️ 빈 임시 음성 채널 삭제됨: '{before.channel.name}' (ID: {before.channel.id})")
                except discord.Forbidden:
                    self.logger.error(
                        f"❌ 빈 임시 채널 {before.channel.name} ({before.channel.id}) 삭제 권한이 없습니다. 봇 권한을 확인해주세요.")
                except Exception as e:
                    self.logger.error(
                        f"❌ 빈 임시 채널 '{before.channel.name}' ({before.channel.id}) 삭제 실패: {e}\n{traceback.format_exc()}")
            else:
                self.logger.debug(f"음성 채널 '{before.channel.name}' (ID: {before.channel.id})에 아직 멤버가 있어 삭제하지 않습니다.")

async def setup(bot):
    await bot.add_cog(TempVoice(bot))
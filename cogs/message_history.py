import discord
from discord.ext import commands
from datetime import datetime, timezone
import traceback

from utils import config  # Import the config module
from utils.logger import get_logger  # Import the centralized get_logger function


class MessageLogCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        # Get the log channel ID from the config module
        self.log_channel_id = config.LOG_CHANNEL_ID
        # Use the centralized logger from utils.logger
        self.logger = get_logger(self.__class__.__name__)
        self.logger.info("MessageLogCog 초기화 완료.")

    @commands.Cog.listener()
    async def on_message_delete(self, message: discord.Message):
        """
        메시지가 삭제될 때 로그를 남깁니다.
        봇 메시지와 로그 채널 자체의 메시지는 무시합니다.
        """
        # 봇 메시지는 무시하여 봇의 자체 삭제 또는 무한 루프를 방지합니다.
        if message.author.bot:
            return

        # 로그 채널 자체의 메시지는 무시합니다.
        if message.channel.id == self.log_channel_id:
            return

        log_channel = self.bot.get_channel(self.log_channel_id)
        if not log_channel:
            self.logger.error(f"로그 채널 ID {self.log_channel_id}을(를) 찾을 수 없습니다.")
            return

        try:
            embed = discord.Embed(
                title="🗑️ 메시지 삭제됨",
                color=discord.Color.red(),
                timestamp=datetime.now(timezone.utc)
            )
            embed.add_field(name="작성자", value=f"{message.author.mention} ({message.author.id})", inline=False)
            embed.add_field(name="채널", value=f"{message.channel.mention} ({message.channel.id})", inline=False)

            # 임베드 필드 값 제한에 맞춰 내용 자르기
            content = message.content
            if len(content) > 1024:  # Discord 임베드 필드 값 제한
                content = content[:1021] + "..."
            embed.add_field(name="내용", value=content if content else "*내용 없음 (예: 임베드만)*", inline=False)

            embed.set_footer(text=f"메시지 ID: {message.id}")
            embed.set_thumbnail(url=message.author.display_avatar.url)

            await log_channel.send(embed=embed)
            self.logger.info(f"{message.channel.name} 채널에서 {message.author.display_name}의 삭제된 메시지 로그를 남겼습니다.")

        except discord.Forbidden:
            self.logger.error(f"봇이 로그 채널 {log_channel.name}에 메시지를 보낼 권한이 없습니다.")
        except Exception as e:
            self.logger.error(f"삭제된 메시지 로깅 중 오류 발생: {e}\n{traceback.format_exc()}")

    @commands.Cog.listener()
    async def on_message_edit(self, before: discord.Message, after: discord.Message):
        """
        메시지가 수정될 때 로그를 남깁니다.
        부분 메시지 처리, 봇 메시지 무시, 로그 채널 메시지 무시.
        """
        # 봇 메시지는 무시합니다.
        if before.author.bot:
            return

        # 로그 채널 자체의 메시지는 무시합니다.
        if before.channel.id == self.log_channel_id:
            return

        # 내용이 실제로 변경되지 않았다면 무시합니다 (예: 임베드 추가/제거, 고정 상태 변경).
        if before.content == after.content:
            return

        log_channel = self.bot.get_channel(self.log_channel_id)
        if not log_channel:
            self.logger.error(f"로그 채널 ID {self.log_channel_id}을(를) 찾을 수 없습니다.")
            return

        try:
            # 'before' 메시지가 부분 메시지인 경우 (예: 캐시에서), 완전히 가져옵니다.
            if before.partial:
                try:
                    before = await before.channel.fetch_message(before.id)
                except discord.NotFound:
                    self.logger.warning(f"수정 로깅을 위한 원본 메시지 {before.id}을(를) 찾을 수 없습니다.")
                    return
                except discord.Forbidden:
                    self.logger.warning(f"봇이 수정 로깅을 위한 메시지 {before.id}을(를) 가져올 권한이 없습니다.")
                    return

            embed = discord.Embed(
                title="✏️ 메시지 수정됨",
                color=discord.Color.blue(),
                timestamp=datetime.now(timezone.utc)
            )
            embed.add_field(name="작성자", value=f"{before.author.mention} ({before.author.id})", inline=False)
            embed.add_field(name="채널", value=f"{before.channel.mention} ({before.channel.id})", inline=False)
            embed.add_field(name="원본 내용", value=before.content if before.content else "*내용 없음*", inline=False)
            embed.add_field(name="새로운 내용", value=after.content if after.content else "*내용 없음*", inline=False)
            embed.set_footer(text=f"메시지 ID: {before.id}")
            embed.set_thumbnail(url=before.author.display_avatar.url)
            embed.url = after.jump_url  # 수정된 메시지로 바로 이동하는 URL 추가

            await log_channel.send(embed=embed)
            self.logger.info(f"{before.channel.name} 채널에서 {before.author.display_name}의 수정된 메시지 로그를 남겼습니다.")

        except discord.Forbidden:
            self.logger.error(f"봇이 로그 채널 {log_channel.name}에 메시지를 보낼 권한이 없습니다.")
        except Exception as e:
            self.logger.error(f"수정된 메시지 로깅 중 오류 발생: {e}\n{traceback.format_exc()}")


async def setup(bot):
    """봇에 MessageLogCog를 추가합니다."""
    await bot.add_cog(MessageLogCog(bot))
    print("MessageLogCog가 성공적으로 로드되었습니다.")


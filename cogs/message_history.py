import discord
from discord.ext import commands
from datetime import datetime, timezone
import traceback
import aiohttp # For downloading attachments

from utils import config
from utils.logger import get_logger


class MessageLogCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.log_channel_id = config.LOG_CHANNEL_ID
        self.logger = get_logger(self.__class__.__name__)
        self.logger.info("MessageLogCog 초기화 완료.")

    @commands.Cog.listener()
    async def on_message_delete(self, message: discord.Message):
        """
        메시지가 삭제될 때 로그를 남기고, 첨부된 미디어를 저장합니다.
        봇 메시지와 로그 채널 자체의 메시지는 무시합니다.
        """
        if message.author.bot:
            return

        if message.guild is None or message.channel.id == self.log_channel_id:
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

            content = message.content
            if len(content) > 1024:
                content = content[:1021] + "..."
            embed.add_field(name="내용", value=content if content else "*내용 없음 (예: 임베드만)*", inline=False)

            # --- 첨부 파일 처리 시작 ---
            if message.attachments:
                attachment_info = []
                for attachment in message.attachments:
                    # Try to send the file directly to the log channel
                    try:
                        # Use aiohttp to download the file directly from the URL
                        async with aiohttp.ClientSession() as session:
                            async with session.get(attachment.url) as resp:
                                if resp.status == 200:
                                    file_bytes = await resp.read()
                                    discord_file = discord.File(
                                        fp=file_bytes,
                                        filename=attachment.filename,
                                        description=f"삭제된 메시지 첨부 파일 (ID: {message.id})"
                                    )
                                    await log_channel.send(f"첨부 파일: `{attachment.filename}`", file=discord_file)
                                    attachment_info.append(f"[`{attachment.filename}`]({attachment.url}) (저장됨)")
                                else:
                                    attachment_info.append(f"[`{attachment.filename}`]({attachment.url}) (저장 실패: HTTP {resp.status})")
                                    self.logger.warning(f"첨부 파일 {attachment.filename} 다운로드 실패: HTTP {resp.status}")
                    except Exception as e:
                        attachment_info.append(f"[`{attachment.filename}`]({attachment.url}) (저장 오류)")
                        self.logger.error(f"첨부 파일 {attachment.filename} 저장 중 오류 발생: {e}")
                embed.add_field(name="첨부 파일", value="\n".join(attachment_info) if attachment_info else "*없음*", inline=False)
            else:
                embed.add_field(name="첨부 파일", value="*없음*", inline=False)
            # --- 첨부 파일 처리 끝 ---

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
        if before.author.bot:
            return

        if before.guild is None or before.channel.id == self.log_channel_id:
            return

        if before.content == after.content and before.attachments == after.attachments: # Also check for attachment changes
            return

        log_channel = self.bot.get_channel(self.log_channel_id)
        if not log_channel:
            self.logger.error(f"로그 채널 ID {self.log_channel_id}을(를) 찾을 수 없습니다.")
            return

        try:
            original_content = before.content

            if isinstance(before, discord.PartialMessage):
                try:
                    before = await before.channel.fetch_message(before.id)
                except discord.NotFound:
                    self.logger.warning(f"수정 로깅을 위한 원본 메시지 {before.id}을(를) 찾을 수 없습니다.")
                    pass
                except discord.Forbidden:
                    self.logger.warning(f"봇이 수정 로깅을 위한 메시지 {before.id}을(를) 가져올 권한이 없습니다.")
                    pass

            embed = discord.Embed(
                title="✏️ 메시지 수정됨",
                color=discord.Color.blue(),
                timestamp=datetime.now(timezone.utc)
            )
            embed.add_field(name="작성자", value=f"{before.author.mention} ({before.author.id})", inline=False)
            embed.add_field(name="채널", value=f"{before.channel.mention} ({before.channel.id})", inline=False)

            old_content_display = original_content
            if len(old_content_display) > 1024:
                old_content_display = old_content_display[:1021] + "..."
            embed.add_field(name="원본 내용", value=old_content_display if old_content_display else "*내용 없음*", inline=False)

            new_content_display = after.content
            if len(new_content_display) > 1024:
                new_content_display = new_content_display[:1021] + "..."
            embed.add_field(name="새로운 내용", value=new_content_display if new_content_display else "*내용 없음*",
                            inline=False)

            # --- 첨부 파일 변경 로깅 ---
            if before.attachments or after.attachments:
                before_attachments = {a.filename for a in before.attachments}
                after_attachments = {a.filename for a in after.attachments}

                added_attachments = after_attachments - before_attachments
                removed_attachments = before_attachments - after_attachments

                attachment_changes = []
                if added_attachments:
                    attachment_changes.append(f"**추가됨:** {', '.join(added_attachments)}")
                if removed_attachments:
                    attachment_changes.append(f"**삭제됨:** {', '.join(removed_attachments)}")
                if attachment_changes:
                    embed.add_field(name="첨부 파일 변경", value="\n".join(attachment_changes), inline=False)
            # --- 첨부 파일 변경 로깅 끝 ---

            embed.set_footer(text=f"메시지 ID: {before.id}")
            embed.set_thumbnail(url=before.author.display_avatar.url)
            embed.url = after.jump_url

            await log_channel.send(embed=embed)
            self.logger.info(f"{before.channel.name} 채널에서 {before.author.display_name}의 수정된 메시지 로그를 남겼습니다.")

        except discord.Forbidden:
            self.logger.error(f"봇이 로그 채널 {log_channel.name}에 메시지를 보낼 권한이 없습니다.")
        except Exception as e:
            self.logger.error(f"수정된 메시지 로깅 중 오류 발생: {e}\n{traceback.format_exc()}")


async def setup(bot):
    await bot.add_cog(MessageLogCog(bot))
    print("MessageLogCog가 성공적으로 로드되었습니다.")
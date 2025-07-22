import discord
from discord.ext import commands
from datetime import datetime, timezone, timedelta
import traceback
import aiohttp  # For downloading attachments

from utils import config
from utils.logger import get_logger


class MessageLogCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.log_channel_id = config.LOG_CHANNEL_ID
        self.logger = get_logger("메세지 기록")
        self.logger.info("메시지 로그 기능이 초기화되었습니다.")
        # Flag to ensure the bot ready message is sent only once
        self._sent_ready_message = False

    async def _send_attachment_to_log(self, log_channel, attachment, message_id, description_prefix=""):
        """Helper function to download and send an attachment to the log channel."""
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(attachment.url) as resp:
                    if resp.status == 200:
                        file_bytes = await resp.read()
                        discord_file = discord.File(
                            fp=file_bytes,
                            filename=attachment.filename,
                            description=f"{description_prefix}첨부 파일 (메시지 ID: {message_id})"
                        )
                        await log_channel.send(f"{description_prefix}첨부 파일: `{attachment.filename}`", file=discord_file)
                        return f"[`{attachment.filename}`]({attachment.url}) (저장됨)"
                    else:
                        self.logger.warning(f"첨부 파일 {attachment.filename} 다운로드 실패: HTTP {resp.status}")
                        return f"[`{attachment.filename}`]({attachment.url}) (저장 실패: HTTP {resp.status})"
        except Exception as e:
            self.logger.error(f"첨부 파일 {attachment.filename} 저장 중 오류 발생: {e}")
            return f"[`{attachment.filename}`]({attachment.url}) (저장 오류)"

    @commands.Cog.listener()
    async def on_ready(self):
        """
        봇이 Discord에 완전히 로그인되고 준비될 때 실행됩니다.
        로그 채널에 봇 시작 메시지를 보냅니다.
        """
        if not self._sent_ready_message:
            self.logger.info(f"{self.bot.user.name} 봇이 온라인 상태입니다!")
            log_channel = self.bot.get_channel(self.log_channel_id)
            if log_channel:
                try:
                    embed = discord.Embed(
                        title="✅ 봇 온라인",
                        description=f"{self.bot.user.name} 봇이 성공적으로 시작되었고 온라인 상태입니다.",
                        color=discord.Color.green(),
                        timestamp=datetime.now(timezone.utc)
                    )
                    embed.add_field(name="봇 ID", value=self.bot.user.id, inline=True)
                    # Current time in KST (Korean Standard Time)
                    embed.add_field(name="현재 시간", value=datetime.now(timezone.utc).astimezone(timezone(timedelta(hours=9))).strftime("%Y-%m-%d %H:%M:%S KST"), inline=True)
                    embed.set_footer(text="메시지 로깅 기능 활성화됨")
                    embed.set_thumbnail(url=self.bot.user.display_avatar.url)

                    await log_channel.send(embed=embed)
                    self.logger.info("로그 채널에 봇 시작 메시지를 성공적으로 보냈습니다.")
                    self._sent_ready_message = True
                except discord.Forbidden:
                    self.logger.error(f"봇이 로그 채널 {log_channel.name}에 메시지를 보낼 권한이 없습니다.")
                except Exception as e:
                    self.logger.error(f"봇 시작 메시지 로깅 중 오류 발생: {e}\n{traceback.format_exc()}")
            else:
                self.logger.error(f"로그 채널 ID {self.log_channel_id}을(를) 찾을 수 없어 봇 시작 메시지를 보낼 수 없습니다.")

    @commands.Cog.listener()
    async def on_message_delete(self, message: discord.Message):
        """
        메시지가 삭제될 때 로그를 남기고, 첨부된 미디어를 저장합니다.
        봇 메시지와 로그 채널 자체의 메시지는 무시합니다.
        """
        # Ignore bot's own messages
        if message.author.bot:
            return

        # Ignore messages in DMs or in the log channel itself
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
            embed.add_field(name="내용", value=content if content else "*내용 없음*", inline=False)

            # --- 첨부 파일 처리 ---
            if message.attachments:
                attachment_info = []
                for attachment in message.attachments:
                    result = await self._send_attachment_to_log(log_channel, attachment, message.id, "삭제된 메시지의 ")
                    attachment_info.append(result)
                embed.add_field(name="첨부 파일", value="\n".join(attachment_info), inline=False)
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
        메시지가 수정될 때 로그를 남기고, 첨부 파일 변경 사항을 기록하며
        삭제되거나 변경된 첨부 파일도 저장합니다.
        """
        # Ignore bot's own message edits
        if before.author.bot:
            return

        # Ignore messages in DMs or in the log channel itself
        if before.guild is None or before.channel.id == self.log_channel_id:
            return

        # Store original content and attachments from 'before' message
        # This is critical for detecting changes accurately, especially with partial messages.
        original_before_attachments = before.attachments
        original_before_content = before.content

        # If 'before' is a PartialMessage (not in cache), try to fetch the full message
        if isinstance(before, discord.PartialMessage):
            try:
                fetched_before = await before.channel.fetch_message(before.id)
                original_before_attachments = fetched_before.attachments
                original_before_content = fetched_before.content
            except (discord.NotFound, discord.Forbidden):
                self.logger.warning(f"수정 로깅을 위한 원본 메시지 {before.id}을(를) 가져올 수 없습니다. 캐시된 정보로 진행합니다.")
                # Proceed with whatever information was available in the partial message if fetching fails
                pass

        # If neither content nor attachments changed, there's nothing to log
        if original_before_content == after.content and original_before_attachments == after.attachments:
            return

        log_channel = self.bot.get_channel(self.log_channel_id)
        if not log_channel:
            self.logger.error(f"로그 채널 ID {self.log_channel_id}을(를) 찾을 수 없습니다.")
            return

        try:
            embed = discord.Embed(
                title="✏️ 메시지 수정됨",
                color=discord.Color.blue(),
                timestamp=datetime.now(timezone.utc)
            )
            embed.add_field(name="작성자", value=f"{before.author.mention} ({before.author.id})", inline=False)
            embed.add_field(name="채널", value=f"{before.channel.mention} ({before.channel.id})", inline=False)

            # Display original and new content, truncating if too long
            old_content_display = original_before_content
            if len(old_content_display) > 1024:
                old_content_display = old_content_display[:1021] + "..."
            embed.add_field(name="원본 내용", value=old_content_display if old_content_display else "*내용 없음*", inline=False)

            new_content_display = after.content
            if len(new_content_display) > 1024:
                new_content_display = new_content_display[:1021] + "..."
            embed.add_field(name="새로운 내용", value=new_content_display if new_content_display else "*내용 없음*",
                            inline=False)

            # --- 첨부 파일 변경 로깅 및 저장 ---
            before_attachment_filenames = {a.filename for a in original_before_attachments}
            after_attachment_filenames = {a.filename for a in after.attachments}

            added_attachments = [a for a in after.attachments if a.filename not in before_attachment_filenames]
            removed_attachments = [a for a in original_before_attachments if
                                   a.filename not in after_attachment_filenames]

            attachment_changes_text = []

            # Log and save removed attachments
            if removed_attachments:
                removed_attachment_info = []
                for attachment in removed_attachments:
                    result = await self._send_attachment_to_log(log_channel, attachment, before.id, "삭제된 첨부 파일: ")
                    removed_attachment_info.append(result)
                attachment_changes_text.append(f"**삭제됨:**\n" + '\n'.join(removed_attachment_info))

            # Log added attachments (can also save them by calling _send_attachment_to_log)
            if added_attachments:
                added_attachment_info = []
                for attachment in added_attachments:
                    added_attachment_info.append(f"[`{attachment.filename}`]({attachment.url})")
                attachment_changes_text.append(f"**추가됨:**\n" + '\n'.join(added_attachment_info))

            # Add attachment changes field to embed
            if attachment_changes_text:
                embed.add_field(name="첨부 파일 변경", value="\n".join(attachment_changes_text), inline=False)
            # Special case: all attachments removed
            elif original_before_attachments and not after.attachments:
                all_removed_info = []
                for attachment in original_before_attachments:
                    result = await self._send_attachment_to_log(log_channel, attachment, before.id, "모두 삭제된 첨부 파일: ")
                    all_removed_info.append(result)
                embed.add_field(name="첨부 파일 변경", value=f"**모든 첨부 파일 삭제됨:**\n" + '\n'.join(all_removed_info),
                                inline=False)
            # Special case: all new attachments added
            elif not original_before_attachments and after.attachments:
                all_added_info = []
                for attachment in after.attachments:
                    all_added_info.append(f"[`{attachment.filename}`]({attachment.url})")
                embed.add_field(name="첨부 파일 변경", value=f"**새로운 첨부 파일 추가됨:**\n" + '\n'.join(all_added_info),
                                inline=False)
            # --- 첨부 파일 변경 로깅 및 저장 끝 ---

            embed.set_footer(text=f"메시지 ID: {before.id}")
            embed.set_thumbnail(url=before.author.display_avatar.url)
            embed.url = after.jump_url # Link to the edited message

            await log_channel.send(embed=embed)
            self.logger.info(f"{before.channel.name} 채널에서 {before.author.display_name}의 수정된 메시지 로그를 남겼습니다.")

        except discord.Forbidden:
            self.logger.error(f"봇이 로그 채널 {log_channel.name}에 메시지를 보낼 권한이 없습니다.")
        except Exception as e:
            self.logger.error(f"수정된 메시지 로깅 중 오류 발생: {e}\n{traceback.format_exc()}")


async def setup(bot):
    await bot.add_cog(MessageLogCog(bot))
    # Removed the print statement here, as on_ready will now handle bot online message
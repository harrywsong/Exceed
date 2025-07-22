# cogs/message_history.py

import discord
from discord.ext import commands
from datetime import datetime, timezone, timedelta
import traceback
import aiohttp  # For downloading attachments
import io  # Import io for BytesIO

from utils import config
from utils.logger import get_logger


class MessageLogCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.log_channel_id = config.MESSAGE_HISTORY_CHANNEL_ID
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
                            fp=io.BytesIO(file_bytes),
                            filename=attachment.filename,
                            description=f"{description_prefix}첨부 파일 (메시지 ID: {message_id})"
                        )
                        await log_channel.send(f"{description_prefix}첨부 파일: `{attachment.filename}`", file=discord_file)
                        self.logger.debug(f"Successfully sent attachment {attachment.filename} to log channel.")
                        return f"[`{attachment.filename}`]({attachment.url}) (저장됨)"
                    else:
                        self.logger.warning(
                            f"첨부 파일 {attachment.filename} 다운로드 실패: HTTP {resp.status}")
                        return f"[`{attachment.filename}`]({attachment.url}) (저장 실패: HTTP {resp.status})"
        except Exception as e:
            self.logger.error(
                f"첨부 파일 {attachment.filename} 저장 중 예외 발생: {e}\n{traceback.format_exc()}")
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
                    embed.add_field(name="현재 시간",
                                    value=datetime.now(timezone.utc).astimezone(timezone(timedelta(hours=9))).strftime(
                                        "%Y-%m-%d %H:%M:%S KST"), inline=True)
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
        self.logger.debug(
            f"Event triggered for message ID {message.id}. Author: {message.author}, Channel: {message.channel}")
        # Ignore bot's own messages
        if message.author and message.author.bot:  # Check if author exists before checking bot status
            self.logger.debug(f"Ignoring bot's own message.")
            return

        # Ignore messages in DMs or in the log channel itself
        if message.guild is None or (message.channel and message.channel.id == self.log_channel_id):
            self.logger.debug(f"Ignoring message in DM or log channel.")
            return

        log_channel = self.bot.get_channel(self.log_channel_id)
        if not log_channel:
            self.logger.error(
                f"로그 채널 ID {self.log_channel_id}을(를) 찾을 수 없습니다. 메시지 삭제 로그를 보낼 수 없습니다.")
            return

        # Try to fetch the full message content if its content is None (common for older messages or messages not in cache)
        full_message = message  # Start with the given message object

        if full_message.content is None:
            self.logger.info(
                f"메시지 {message.id} 내용이 None입니다. 전체 메시지를 가져오려 합니다.")
            try:
                # Attempt to fetch the full message object from Discord API
                # Ensure message.channel is not None before fetching
                if message.channel:
                    fetched_msg = await message.channel.fetch_message(message.id)
                    full_message = fetched_msg  # Use the fetched message
                    self.logger.info(
                        f"메시지 {message.id}를 성공적으로 가져왔습니다. 내용 길이: {len(full_message.content) if full_message.content else 0}.")
                else:
                    self.logger.warning(
                        f"메시지 {message.id}에 채널 정보가 없어 내용을 가져올 수 없습니다.")
            except (discord.NotFound, discord.Forbidden):
                self.logger.warning(
                    f"메시지 {message.id}를 가져오는 데 실패했습니다 (NotFound/Forbidden). 내용이 부정확할 수 있습니다.")
            except Exception as e:
                self.logger.error(
                    f"메시지 {message.id}를 가져오는 중 예상치 못한 오류 발생: {e}\n{traceback.format_exc()}")
        else:
            self.logger.debug(
                f"메시지 {message.id}의 내용이 이벤트에 포함되어 있습니다. 내용 길이: {len(message.content) if message.content else 0}.")

        try:
            embed = discord.Embed(
                title="🗑️ 메시지 삭제됨",
                color=discord.Color.red(),
                timestamp=datetime.now(timezone.utc)
            )
            # Use data from full_message which might be fetched
            author_mention = full_message.author.mention if full_message.author else "알 수 없는 사용자"
            author_id = full_message.author.id if full_message.author else "N/A"
            channel_mention = full_message.channel.mention if full_message.channel else "알 수 없는 채널"
            channel_id = full_message.channel.id if full_message.channel else "N/A"
            author_avatar_url = full_message.author.display_avatar.url if full_message.author and full_message.author.display_avatar else None

            # Mention the user in the "작성자" field
            embed.add_field(name="작성자", value=f"{author_mention} ({author_id})", inline=False)
            embed.add_field(name="채널", value=f"{channel_mention} ({channel_id})", inline=False)

            # Ensure content is always a string for display and length check
            content_to_display = full_message.content if full_message.content is not None else "*내용 없음*"
            if len(content_to_display) > 1024:
                content_to_display = content_to_display[:1021] + "..."
            embed.add_field(name="내용", value=content_to_display, inline=False)

            # --- 첨부 파일 처리 ---
            if full_message.attachments:
                attachment_info = []
                for attachment in full_message.attachments:
                    result = await self._send_attachment_to_log(log_channel, attachment, full_message.id, "삭제된 메시지의 ")
                    attachment_info.append(result)
                embed.add_field(name="첨부 파일", value="\n".join(attachment_info), inline=False)
            else:
                embed.add_field(name="첨부 파일", value="*없음*", inline=False)
            # --- 첨부 파일 처리 끝 ---

            embed.set_footer(text=f"메시지 ID: {full_message.id}")
            if author_avatar_url:
                embed.set_thumbnail(url=author_avatar_url)

            await log_channel.send(embed=embed)
            self.logger.info(
                f"{full_message.channel.name if full_message.channel else '알 수 없는 채널'}에서 {author_mention}의 삭제된 메시지를 기록했습니다.")

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
        self.logger.debug(
            f"Event triggered for message ID {before.id}. Author: {before.author}, Channel: {before.channel}")
        # Ignore bot's own message edits
        if before.author and before.author.bot:  # Check if author exists before checking bot status
            self.logger.debug(
                f"Ignoring bot's own message edit by {before.author.display_name}.")
            return

        # Ignore messages in DMs or in the log channel itself
        if before.guild is None or (before.channel and before.channel.id == self.log_channel_id):
            self.logger.debug(
                f"Ignoring message edit in DM or log channel (ID: {before.channel.id}).")
            return

        # --- Attempt to get reliable 'before' content and attachments ---
        fetched_original_content = before.content
        fetched_original_attachments = before.attachments  # Initialize with what's available in 'before'

        # If 'before' message content is None (not in cache or content not provided by Discord)
        if fetched_original_content is None:
            self.logger.info(
                f"'before' 메시지 {before.id} 내용이 None입니다. 전체 메시지를 가져오려 합니다.")
            try:
                # Attempt to fetch the full message object from Discord API
                # Ensure before.channel is not None before fetching
                if before.channel:
                    fetched_before_message = await before.channel.fetch_message(before.id)
                    fetched_original_content = fetched_before_message.content if fetched_before_message.content is not None else ""
                    fetched_original_attachments = fetched_before_message.attachments
                    self.logger.info(
                        f"'before' 메시지 {before.id}를 성공적으로 가져왔습니다. 내용 길이: {len(fetched_original_content)}자.")
                else:
                    self.logger.warning(
                        f"'before' 메시지 {before.id}에 채널 정보가 없어 내용을 가져올 수 없습니다.")
            except (discord.NotFound, discord.Forbidden):
                self.logger.warning(
                    f"'before' 메시지 {before.id}를 가져오는 데 실패했습니다 (NotFound/Forbidden). 원래 내용이 부정확할 수 있습니다.")
                fetched_original_content = "*캐시에 없거나 가져올 수 없는 내용*"
            except Exception as e:
                self.logger.error(
                    f"'before' 메시지 {before.id}를 가져오는 중 예상치 못한 오류 발생: {e}\n{traceback.format_exc()}")
                fetched_original_content = "*가져오기 실패 (오류 발생)*"
        else:
            self.logger.debug(
                f"'before' 메시지 {before.id}의 내용이 이벤트에 포함되어 있습니다. 내용 길이: {len(fetched_original_content)}.")

            # Ensure after.content is always a string for safe comparison and display
        after_content = after.content if after.content is not None else ""

        # Content and attachment comparison
        # Use .strip() to ignore leading/trailing whitespace differences
        content_changed = (fetched_original_content.strip() != after_content.strip())

        # Compare the attachments lists obtained from the most complete 'before' and 'after' objects
        attachments_changed = (fetched_original_attachments != after.attachments)

        self.logger.debug(f"Content changed: {content_changed}")
        self.logger.debug(f"Attachments changed: {attachments_changed}")
        self.logger.debug(
            f"Before content (fetched/cached): '{fetched_original_content[:100]}'")
        self.logger.debug(f"After content: '{after_content[:100]}'")

        if not content_changed and not attachments_changed:
            self.logger.debug(
                f"No significant content or attachment changes detected for message {before.id}. Returning.")
            return

        log_channel = self.bot.get_channel(self.log_channel_id)
        if not log_channel:
            self.logger.error(
                f"로그 채널 ID {self.log_channel_id}을(를) 찾을 수 없습니다. 메시지 수정 로그를 보낼 수 없습니다.")
            return

        try:
            embed = discord.Embed(
                title="✏️ 메시지 수정됨",
                color=discord.Color.blue(),
                timestamp=datetime.now(timezone.utc)
            )
            # Use author/channel info from 'before' message, as it's typically available
            author_mention = before.author.mention if before.author else "알 수 없는 사용자"
            author_id = before.author.id if before.author else "N/A"
            channel_mention = before.channel.mention if before.channel else "알 수 없는 채널"
            channel_id = before.channel.id if before.channel else "N/A"
            author_avatar_url = before.author.display_avatar.url if before.author and before.author.display_avatar else None

            # Mention the user in the "작성자" field
            embed.add_field(name="작성자", value=f"{author_mention} ({author_id})", inline=False)
            embed.add_field(name="채널", value=f"{channel_mention} ({channel_id})", inline=False)

            # Display original and new content, truncating if too long
            old_content_display = str(fetched_original_content)  # Ensure it's a string
            if len(old_content_display) > 1024:
                old_content_display = old_content_display[:1021] + "..."
            embed.add_field(name="원본 내용", value=old_content_display if old_content_display else "*내용 없음*", inline=False)

            new_content_display = str(after_content)  # Ensure it's a string
            if len(new_content_display) > 1024:
                new_content_display = new_content_display[:1021] + "..."
            embed.add_field(name="새로운 내용", value=new_content_display if new_content_display else "*내용 없음*",
                            inline=False)

            # --- 첨부 파일 변경 로깅 및 저장 ---
            before_attachment_filenames = {a.filename for a in fetched_original_attachments}
            after_attachment_filenames = {a.filename for a in after.attachments}

            added_attachments = [a for a in after.attachments if a.filename not in before_attachment_filenames]
            removed_attachments = [a for a in fetched_original_attachments if
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
            elif fetched_original_attachments and not after.attachments:
                all_removed_info = []
                for attachment in fetched_original_attachments:
                    result = await self._send_attachment_to_log(log_channel, attachment, before.id, "모두 삭제된 첨부 파일: ")
                    all_removed_info.append(result)
                embed.add_field(name="첨부 파일 변경", value=f"**모든 첨부 파일 삭제됨:**\n" + '\n'.join(all_removed_info),
                                inline=False)
            # Special case: all new attachments added
            elif not fetched_original_attachments and after.attachments:
                all_added_info = []
                for attachment in after.attachments:
                    all_added_info.append(f"[`{attachment.filename}`]({attachment.url})")
                embed.add_field(name="첨부 파일 변경", value=f"**새로운 첨부 파일 추가됨:**\n" + '\n'.join(all_added_info),
                                inline=False)
            # --- 첨부 파일 변경 로깅 및 저장 끝 ---

            embed.set_footer(text=f"메시지 ID: {before.id}")
            if author_avatar_url:
                embed.set_thumbnail(url=author_avatar_url)
            embed.url = after.jump_url  # Link to the edited message

            await log_channel.send(embed=embed)
            self.logger.info(
                f"{before.channel.name if before.channel else '알 수 없는 채널'}에서 {author_mention}의 수정된 메시지를 기록했습니다.")

        except discord.Forbidden:
            self.logger.error(f"봇이 로그 채널 {log_channel.name}에 메시지를 보낼 권한이 없습니다.")
        except Exception as e:
            self.logger.error(f"수정된 메시지 로깅 중 오류 발생: {e}\n{traceback.format_exc()}")


async def setup(bot):
    await bot.add_cog(MessageLogCog(bot))
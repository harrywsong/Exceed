# cogs/message_history.py

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
                        self.logger.debug(f"DEBUG: Successfully sent attachment {attachment.filename} to log channel.")
                        return f"[`{attachment.filename}`]({attachment.url}) (저장됨)"
                    else:
                        self.logger.warning(
                            f"WARNING: Attachment {attachment.filename} download failed: HTTP {resp.status}")
                        return f"[`{attachment.filename}`]({attachment.url}) (저장 실패: HTTP {resp.status})"
        except Exception as e:
            self.logger.error(
                f"ERROR: Exception while saving attachment {attachment.filename}: {e}\n{traceback.format_exc()}")
            return f"[`{attachment.filename}`]({attachment.url}) (저장 오류)"

    @commands.Cog.listener()
    async def on_ready(self):
        """
        봇이 Discord에 완전히 로그인되고 준비될 때 실행됩니다.
        로그 채널에 봇 시작 메시지를 보냅니다.
        """
        if not self._sent_ready_message:
            self.logger.info(f"INFO: {self.bot.user.name} 봇이 온라인 상태입니다!")
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
                    self.logger.info("INFO: 로그 채널에 봇 시작 메시지를 성공적으로 보냈습니다.")
                    self._sent_ready_message = True
                except discord.Forbidden:
                    self.logger.error(f"ERROR: 봇이 로그 채널 {log_channel.name}에 메시지를 보낼 권한이 없습니다.")
                except Exception as e:
                    self.logger.error(f"ERROR: 봇 시작 메시지 로깅 중 오류 발생: {e}\n{traceback.format_exc()}")
            else:
                self.logger.error(f"ERROR: 로그 채널 ID {self.log_channel_id}을(를) 찾을 수 없어 봇 시작 메시지를 보낼 수 없습니다.")

    @commands.Cog.listener()
    async def on_message_delete(self, message: discord.Message):
        """
        메시지가 삭제될 때 로그를 남기고, 첨부된 미디어를 저장합니다.
        봇 메시지와 로그 채널 자체의 메시지는 무시합니다.
        """
        self.logger.debug(f"DEBUG (on_message_delete): Event triggered for message ID {message.id}.")
        # Ignore bot's own messages
        if message.author.bot:
            self.logger.debug(f"DEBUG (on_message_delete): Ignoring bot's own message.")
            return

        # Ignore messages in DMs or in the log channel itself
        if message.guild is None or message.channel.id == self.log_channel_id:
            self.logger.debug(f"DEBUG (on_message_delete): Ignoring message in DM or log channel.")
            return

        log_channel = self.bot.get_channel(self.log_channel_id)
        if not log_channel:
            self.logger.error(
                f"ERROR (on_message_delete): 로그 채널 ID {self.log_channel_id}을(를) 찾을 수 없습니다. 메시지 삭제 로그를 보낼 수 없습니다.")
            return

        # Try to fetch the full message content if it's a partial message
        # This is critical for messages deleted when the bot was offline or not cached.
        full_message = message  # Start with the given message object

        # If the message is partial or its content is None (common for older messages)
        if message.partial or message.content is None:
            self.logger.info(
                f"INFO (on_message_delete): Message {message.id} is partial or content is None. Attempting to fetch full message.")
            try:
                # Attempt to fetch the full message object from Discord API
                fetched_msg = await message.channel.fetch_message(message.id)
                full_message = fetched_msg  # Use the fetched message
                self.logger.info(
                    f"INFO (on_message_delete): Successfully fetched full message {message.id}. Content length: {len(full_message.content) if full_message.content else 0}.")
            except (discord.NotFound, discord.Forbidden):
                self.logger.warning(
                    f"WARNING (on_message_delete): Failed to fetch full message {message.id} (NotFound/Forbidden). Content might be inaccurate.")
                # If fetch fails, we'll proceed with the partial info we have
                # In this case, full_message remains the original 'message' object, which might have limited data.
                # The content will be "*내용 없음*" or whatever was available.
            except Exception as e:
                self.logger.error(
                    f"ERROR (on_message_delete): Unexpected error fetching message {message.id}: {e}\n{traceback.format_exc()}")
                # If fetch fails, we'll proceed with the partial info we have
        else:
            self.logger.debug(
                f"DEBUG (on_message_delete): Message {message.id} content available in event. Content length: {len(message.content) if message.content else 0}.")

        try:
            embed = discord.Embed(
                title="🗑️ 메시지 삭제됨",
                color=discord.Color.red(),
                timestamp=datetime.now(timezone.utc)
            )
            embed.add_field(name="작성자",
                            value=f"{full_message.author.mention} ({full_message.author.id})" if full_message.author else "알 수 없는 사용자",
                            inline=False)
            embed.add_field(name="채널",
                            value=f"{full_message.channel.mention} ({full_message.channel.id})" if full_message.channel else "알 수 없는 채널",
                            inline=False)

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
            embed.set_thumbnail(
                url=full_message.author.display_avatar.url if full_message.author and full_message.author.display_avatar else None)

            await log_channel.send(embed=embed)
            self.logger.info(
                f"INFO (on_message_delete): Logged deleted message from {full_message.author.display_name if full_message.author else 'Unknown'} in {full_message.channel.name if full_message.channel else 'Unknown Channel'}.")

        except discord.Forbidden:
            self.logger.error(f"ERROR (on_message_delete): 봇이 로그 채널 {log_channel.name}에 메시지를 보낼 권한이 없습니다.")
        except Exception as e:
            self.logger.error(f"ERROR (on_message_delete): 삭제된 메시지 로깅 중 오류 발생: {e}\n{traceback.format_exc()}")

    @commands.Cog.listener()
    async def on_message_edit(self, before: discord.Message, after: discord.Message):
        """
        메시지가 수정될 때 로그를 남기고, 첨부 파일 변경 사항을 기록하며
        삭제되거나 변경된 첨부 파일도 저장합니다.
        """
        self.logger.debug(f"DEBUG (on_message_edit): Event triggered for message ID {before.id}.")
        # Ignore bot's own message edits
        if before.author.bot:
            self.logger.debug(
                f"DEBUG (on_message_edit): Ignoring bot's own message edit by {before.author.display_name}.")
            return

        # Ignore messages in DMs or in the log channel itself
        if before.guild is None or before.channel.id == self.log_channel_id:
            self.logger.debug(
                f"DEBUG (on_message_edit): Ignoring message edit in DM or log channel (ID: {before.channel.id}).")
            return

        # --- Attempt to get reliable 'before' content and attachments ---
        fetched_original_content = before.content
        fetched_original_attachments = before.attachments  # Initialize with what's available in 'before'

        # If 'before' is a PartialMessage or its content is None (not in cache or content not provided by Discord)
        if before.partial or fetched_original_content is None:
            self.logger.info(
                f"INFO (on_message_edit): 'before' message {before.id} is partial or content is None. Attempting to fetch full message.")
            try:
                # Fetch the full message object from Discord API
                fetched_before_message = await before.channel.fetch_message(before.id)
                fetched_original_content = fetched_before_message.content if fetched_before_message.content is not None else ""
                fetched_original_attachments = fetched_before_message.attachments
                self.logger.info(
                    f"INFO (on_message_edit): Successfully fetched full 'before' message {before.id}. Content length: {len(fetched_original_content)}.")
            except (discord.NotFound, discord.Forbidden):
                self.logger.warning(
                    f"WARNING (on_message_edit): Failed to fetch full 'before' message {before.id} (NotFound/Forbidden). Original content might be inaccurate.")
                # If fetch fails, default to empty string for content, keep available attachments from 'before'
                fetched_original_content = "*캐시에 없거나 가져올 수 없는 내용*"
            except Exception as e:
                self.logger.error(
                    f"ERROR (on_message_edit): Unexpected error fetching 'before' message {before.id}: {e}\n{traceback.format_exc()}")
                fetched_original_content = "*가져오기 실패 (오류 발생)*"
        else:
            self.logger.debug(
                f"DEBUG (on_message_edit): 'before' message {before.id} content available in event. Content length: {len(fetched_original_content)}.")

        # Ensure after.content is always a string for safe comparison and display
        after_content = after.content if after.content is not None else ""

        # Content and attachment comparison
        # Use .strip() to ignore leading/trailing whitespace differences
        content_changed = (fetched_original_content.strip() != after_content.strip())

        # Corrected: Compare the attachments lists obtained from the most complete 'before' and 'after' objects
        attachments_changed = (fetched_original_attachments != after.attachments)

        self.logger.debug(f"DEBUG (on_message_edit): Content changed: {content_changed}")
        self.logger.debug(f"DEBUG (on_message_edit): Attachments changed: {attachments_changed}")
        self.logger.debug(
            f"DEBUG (on_message_edit): Before content (fetched/cached): '{fetched_original_content[:100]}'")
        self.logger.debug(f"DEBUG (on_message_edit): After content: '{after_content[:100]}'")

        if not content_changed and not attachments_changed:
            self.logger.debug(
                f"DEBUG (on_message_edit): No significant content or attachment changes detected for message {before.id}. Returning.")
            return

        log_channel = self.bot.get_channel(self.log_channel_id)
        if not log_channel:
            self.logger.error(
                f"ERROR (on_message_edit): 로그 채널 ID {self.log_channel_id}을(를) 찾을 수 없습니다. 메시지 수정 로그를 보낼 수 없습니다.")
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
            embed.set_thumbnail(url=before.author.display_avatar.url)
            embed.url = after.jump_url  # Link to the edited message

            await log_channel.send(embed=embed)
            self.logger.info(
                f"INFO (on_message_edit): Logged edited message from {before.author.display_name} in {before.channel.name}.")

        except discord.Forbidden:
            self.logger.error(f"ERROR (on_message_edit): 봇이 로그 채널 {log_channel.name}에 메시지를 보낼 권한이 없습니다.")
        except Exception as e:
            self.logger.error(f"ERROR (on_message_edit): 수정된 메시지 로깅 중 오류 발생: {e}\n{traceback.format_exc()}")


async def setup(bot):
    await bot.add_cog(MessageLogCog(bot))
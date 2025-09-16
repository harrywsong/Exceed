# cogs/message_history.py - Updated for multi-server support

import discord
from discord.ext import commands
from datetime import datetime, timezone, timedelta
import traceback
import aiohttp
import io
import os

from utils.config import (
    get_channel_id,
    is_feature_enabled,
    is_server_configured
)
from utils.logger import get_logger


class MessageLogCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.logger = get_logger("메세지 기록")
        self.logger.info("메시지 로그 기능이 초기화되었습니다.")

        # Flag to ensure the bot ready message is sent only once per server
        self._sent_ready_messages = set()

        # Media history folder per server
        self.base_media_folder = "mediahistory"
        os.makedirs(self.base_media_folder, exist_ok=True)

    def get_server_media_folder(self, guild_id: int) -> str:
        """Get media folder path for specific server"""
        folder = os.path.join(self.base_media_folder, str(guild_id))
        os.makedirs(folder, exist_ok=True)
        return folder

    async def _send_attachment_to_log(self, log_channel, attachment, message_id, guild_id, description_prefix=""):
        """Helper function to download, save locally, and send an attachment to the log channel."""
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(attachment.url) as resp:
                    if resp.status == 200:
                        file_bytes = await resp.read()

                        # Save to server-specific mediahistory folder
                        media_folder = self.get_server_media_folder(guild_id)
                        save_path = os.path.join(media_folder, f"{message_id}_{attachment.filename}")

                        with open(save_path, 'wb') as f:
                            f.write(file_bytes)
                        self.logger.debug(f"Successfully saved attachment {attachment.filename} to {save_path}.")

                        discord_file = discord.File(
                            save_path,
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
        각 서버의 로그 채널에 봇 시작 메시지를 보냅니다.
        """
        self.logger.info(f"{self.bot.user.name} 봇이 온라인 상태입니다!")

        # Send ready message to each configured server
        for guild in self.bot.guilds:
            if guild.id not in self._sent_ready_messages and is_server_configured(guild.id) and is_feature_enabled(
                    guild.id, 'message_history'):
                log_channel_id = get_channel_id(guild.id, 'log_channel')
                if log_channel_id:
                    log_channel = self.bot.get_channel(log_channel_id)
                    if log_channel:
                        try:
                            embed = discord.Embed(
                                title="✅ 봇 온라인",
                                description=f"{self.bot.user.name} 봇이 성공적으로 시작되었고 온라인 상태입니다.",
                                color=discord.Color.green(),
                                timestamp=datetime.now(timezone.utc)
                            )
                            embed.add_field(name="봇 ID", value=self.bot.user.id, inline=True)
                            embed.add_field(name="서버", value=f"{guild.name} ({guild.id})", inline=True)
                            embed.add_field(name="현재 시간",
                                            value=datetime.now(timezone.utc).astimezone(
                                                timezone(timedelta(hours=-4))).strftime(
                                                "%Y-%m-%d %H:%M:%S EST"), inline=True)
                            embed.set_footer(text="메시지 로깅 기능 활성화됨")
                            embed.set_thumbnail(url=self.bot.user.display_avatar.url)

                            await log_channel.send(embed=embed)
                            self.logger.info(f"로그 채널에 봇 시작 메시지를 성공적으로 보냈습니다. (서버: {guild.name})")
                            self._sent_ready_messages.add(guild.id)
                        except discord.Forbidden:
                            self.logger.error(f"봇이 로그 채널 {log_channel.name}에 메시지를 보낼 권한이 없습니다. (서버: {guild.name})")
                        except Exception as e:
                            self.logger.error(f"봇 시작 메시지 로깅 중 오류 발생 (서버: {guild.name}): {e}\n{traceback.format_exc()}")
                    else:
                        self.logger.error(
                            f"로그 채널 ID {log_channel_id}을(를) 찾을 수 없어 봇 시작 메시지를 보낼 수 없습니다. (서버: {guild.name})")

    @commands.Cog.listener()
    async def on_message_delete(self, message: discord.Message):
        """
        메시지가 삭제될 때 로그를 남기고, 첨부된 미디어를 저장합니다.
        봇 메시지와 로그 채널 자체의 메시지는 무시합니다.
        """
        # Check if this is in a guild and if message history is enabled
        if not message.guild or not is_server_configured(message.guild.id):
            return

        if not is_feature_enabled(message.guild.id, 'message_history'):
            return

        self.logger.debug(
            f"Event triggered for message ID {message.id}. Author: {message.author}, Channel: {message.channel}, Guild: {message.guild.name}")

        # Ignore bot's own messages
        if message.author and message.author.bot:
            self.logger.debug(f"Ignoring bot's own message.")
            return

        # Get log channel for this server
        log_channel_id = get_channel_id(message.guild.id, 'message_history_channel')
        if not log_channel_id:
            return  # No message history channel configured

        # Ignore messages in the log channel itself
        if message.channel and message.channel.id == log_channel_id:
            self.logger.debug(f"Ignoring message in log channel.")
            return

        log_channel = self.bot.get_channel(log_channel_id)
        if not log_channel:
            self.logger.error(
                f"로그 채널 ID {log_channel_id}을(를) 찾을 수 없습니다. 메시지 삭제 로그를 보낼 수 없습니다. (서버: {message.guild.name})")
            return

        # Try to fetch the full message content if its content is None
        full_message = message

        if full_message.content is None:
            self.logger.info(
                f"메시지 {message.id} 내용이 None입니다. 전체 메시지를 가져오려 합니다.")
            try:
                if message.channel:
                    fetched_msg = await message.channel.fetch_message(message.id)
                    full_message = fetched_msg
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

            author_mention = full_message.author.mention if full_message.author else "알 수 없는 사용자"
            author_id = full_message.author.id if full_message.author else "N/A"
            channel_mention = full_message.channel.mention if full_message.channel else "알 수 없는 채널"
            channel_id = full_message.channel.id if full_message.channel else "N/A"
            author_avatar_url = full_message.author.display_avatar.url if full_message.author and full_message.author.display_avatar else None

            embed.add_field(name="작성자", value=f"{author_mention} ({author_id})", inline=False)
            embed.add_field(name="채널", value=f"{channel_mention} ({channel_id})", inline=False)

            content_to_display = full_message.content if full_message.content is not None else "*내용 없음*"
            if len(content_to_display) > 1024:
                content_to_display = content_to_display[:1021] + "..."
            embed.add_field(name="내용", value=content_to_display, inline=False)

            # Handle attachments
            if full_message.attachments:
                attachment_info = []
                for attachment in full_message.attachments:
                    result = await self._send_attachment_to_log(log_channel, attachment, full_message.id,
                                                                message.guild.id, "삭제된 메시지의 ")
                    attachment_info.append(result)
                embed.add_field(name="첨부 파일", value="\n".join(attachment_info), inline=False)
            else:
                embed.add_field(name="첨부 파일", value="*없음*", inline=False)

            embed.set_footer(text=f"메시지 ID: {full_message.id} • 서버: {message.guild.name}")
            if author_avatar_url:
                embed.set_thumbnail(url=author_avatar_url)

            await log_channel.send(embed=embed)
            self.logger.info(
                f"{full_message.channel.name if full_message.channel else '알 수 없는 채널'}에서 {author_mention}의 삭제된 메시지를 기록했습니다. (서버: {message.guild.name})")

        except discord.Forbidden:
            self.logger.error(f"봇이 로그 채널 {log_channel.name}에 메시지를 보낼 권한이 없습니다. (서버: {message.guild.name})")
        except Exception as e:
            self.logger.error(f"삭제된 메시지 로깅 중 오류 발생 (서버: {message.guild.name}): {e}\n{traceback.format_exc()}")

    @commands.Cog.listener()
    async def on_message_edit(self, before: discord.Message, after: discord.Message):
        """
        메시지가 수정될 때 로그를 남기고, 첨부 파일 변경 사항을 기록하며
        삭제되거나 변경된 첨부 파일도 저장합니다.
        """
        # Check if this is in a guild and if message history is enabled
        if not before.guild or not is_server_configured(before.guild.id):
            return

        if not is_feature_enabled(before.guild.id, 'message_history'):
            return

        self.logger.debug(
            f"Event triggered for message ID {before.id}. Author: {before.author}, Channel: {before.channel}, Guild: {before.guild.name}")

        # Ignore bot's own message edits
        if before.author and before.author.bot:
            self.logger.debug(
                f"Ignoring bot's own message edit by {before.author.display_name}.")
            return

        # Get log channel for this server
        log_channel_id = get_channel_id(before.guild.id, 'message_history_channel')
        if not log_channel_id:
            return  # No message history channel configured

        # Ignore messages in the log channel itself
        if before.channel and before.channel.id == log_channel_id:
            self.logger.debug(
                f"Ignoring message edit in log channel (ID: {before.channel.id}).")
            return

        # Attempt to get reliable 'before' content and attachments
        fetched_original_content = before.content
        fetched_original_attachments = before.attachments

        # If 'before' message content is None (not in cache or content not provided by Discord)
        if fetched_original_content is None:
            self.logger.info(
                f"'before' 메시지 {before.id} 내용이 None입니다. 전체 메시지를 가져오려 합니다.")
            try:
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

        after_content = after.content if after.content is not None else ""

        # Content and attachment comparison
        content_changed = (fetched_original_content.strip() != after_content.strip())
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

        log_channel = self.bot.get_channel(log_channel_id)
        if not log_channel:
            self.logger.error(
                f"로그 채널 ID {log_channel_id}을(를) 찾을 수 없습니다. 메시지 수정 로그를 보낼 수 없습니다. (서버: {before.guild.name})")
            return

        try:
            embed = discord.Embed(
                title="✏️ 메시지 수정됨",
                color=discord.Color.blue(),
                timestamp=datetime.now(timezone.utc)
            )

            author_mention = before.author.mention if before.author else "알 수 없는 사용자"
            author_id = before.author.id if before.author else "N/A"
            channel_mention = before.channel.mention if before.channel else "알 수 없는 채널"
            channel_id = before.channel.id if before.channel else "N/A"
            author_avatar_url = before.author.display_avatar.url if before.author and before.author.display_avatar else None

            embed.add_field(name="작성자", value=f"{author_mention} ({author_id})", inline=False)
            embed.add_field(name="채널", value=f"{channel_mention} ({channel_id})", inline=False)

            old_content_display = str(fetched_original_content)
            if len(old_content_display) > 1024:
                old_content_display = old_content_display[:1021] + "..."
            embed.add_field(name="원본 내용", value=old_content_display if old_content_display else "*내용 없음*", inline=False)

            new_content_display = str(after_content)
            if len(new_content_display) > 1024:
                new_content_display = new_content_display[:1021] + "..."
            embed.add_field(name="새로운 내용", value=new_content_display if new_content_display else "*내용 없음*",
                            inline=False)

            # Handle attachment changes
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
                    result = await self._send_attachment_to_log(log_channel, attachment, before.id, before.guild.id,
                                                                "삭제된 첨부 파일: ")
                    removed_attachment_info.append(result)
                attachment_changes_text.append(f"**삭제됨:**\n" + '\n'.join(removed_attachment_info))

            # Log added attachments
            if added_attachments:
                added_attachment_info = []
                for attachment in added_attachments:
                    added_attachment_info.append(f"[`{attachment.filename}`]({attachment.url})")
                attachment_changes_text.append(f"**추가됨:**\n" + '\n'.join(added_attachment_info))

            # Add attachment changes field to embed
            if attachment_changes_text:
                embed.add_field(name="첨부 파일 변경", value="\n".join(attachment_changes_text), inline=False)
            elif fetched_original_attachments and not after.attachments:
                all_removed_info = []
                for attachment in fetched_original_attachments:
                    result = await self._send_attachment_to_log(log_channel, attachment, before.id, before.guild.id,
                                                                "모두 삭제된 첨부 파일: ")
                    all_removed_info.append(result)
                embed.add_field(name="첨부 파일 변경", value=f"**모든 첨부 파일 삭제됨:**\n" + '\n'.join(all_removed_info),
                                inline=False)
            elif not fetched_original_attachments and after.attachments:
                all_added_info = []
                for attachment in after.attachments:
                    all_added_info.append(f"[`{attachment.filename}`]({attachment.url})")
                embed.add_field(name="첨부 파일 변경", value=f"**새로운 첨부 파일 추가됨:**\n" + '\n'.join(all_added_info),
                                inline=False)

            embed.set_footer(text=f"메시지 ID: {before.id} • 서버: {before.guild.name}")
            if author_avatar_url:
                embed.set_thumbnail(url=author_avatar_url)
            embed.url = after.jump_url  # Link to the edited message

            await log_channel.send(embed=embed)
            self.logger.info(
                f"{before.channel.name if before.channel else '알 수 없는 채널'}에서 {author_mention}의 수정된 메시지를 기록했습니다. (서버: {before.guild.name})")

        except discord.Forbidden:
            self.logger.error(f"봇이 로그 채널 {log_channel.name}에 메시지를 보낼 권한이 없습니다. (서버: {before.guild.name})")
        except Exception as e:
            self.logger.error(f"수정된 메시지 로깅 중 오류 발생 (서버: {before.guild.name}): {e}\n{traceback.format_exc()}")


async def setup(bot):
    await bot.add_cog(MessageLogCog(bot))
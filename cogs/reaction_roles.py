# cogs/reaction_roles.py - Updated for multi-server support
import discord
from discord.ext import commands
import traceback
import asyncio

from utils.logger import get_logger
from utils.config import (
    get_channel_id,
    get_role_id,
    get_reaction_roles,
    set_reaction_roles,
    is_feature_enabled,
    is_server_configured,
    get_server_setting
)


class ReactionRoles(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        # FIX: The logger is now a global singleton, so we just get it by name.
        self.logger = get_logger("리액션 역할")

        # FIX: Removed initial log. It's better to log within a function
        # with guild context, such as populate_reactions_for_guild.

        # Schedule population after bot is fully ready
        self.bot.loop.create_task(self.wait_until_ready_then_populate())

    async def wait_until_ready_then_populate(self):
        await self.bot.wait_until_ready()
        try:
            self.logger.info("리액션 역할 기능이 초기화되었습니다.")
            await self.populate_reactions()
        except Exception as e:
            self.logger.error(f"⛔ ReactionRoles 초기화 중 오류 발생: {e}\n{traceback.format_exc()}")

    async def populate_reactions(self):
        """Populate reactions for all configured servers"""
        for guild in self.bot.guilds:
            if not is_server_configured(guild.id) or not is_feature_enabled(guild.id, 'reaction_roles'):
                continue

            await self.populate_reactions_for_guild(guild)

    async def populate_reactions_for_guild(self, guild: discord.Guild):
        """Populate reactions for a specific guild"""
        try:
            # Get reaction role mapping for this server
            reaction_role_map = get_reaction_roles(guild.id)
            if not reaction_role_map:
                # FIX: Add guild_id to log message for context
                self.logger.info(f"No reaction roles configured for guild {guild.name} ({guild.id})",
                                 extra={'guild_id': guild.id})
                return

            # Check for verification system
            verification_message_id = get_server_setting(guild.id, 'verification_message_id')
            verification_emoji = get_server_setting(guild.id, 'verification_emoji', '✅')
            unverified_role_id = get_role_id(guild.id, 'unverified_role')
            accepted_role_id = get_role_id(guild.id, 'member_role')

            if verification_message_id and unverified_role_id and accepted_role_id:
                await self.setup_verification_reaction(guild, verification_message_id, verification_emoji)

            def format_emoji_for_map_key(e):
                """Format the emoji or reaction emoji into the simplified key matching your config."""
                if isinstance(e, str):
                    return e  # raw unicode emoji like '🇼'

                if getattr(e, "id", None):  # Custom emoji
                    return f"<:{e.name.lower()}:{e.id}>"
                else:
                    # Unicode emoji, return str
                    return str(e)

            # Process reaction role messages
            for message_id, emoji_role_map in reaction_role_map.items():
                message = await self.find_message_in_guild(guild, message_id)
                if not message:
                    self.logger.error(f"⛔ 메시지 ID {message_id}을(를) 길드 {guild.name}에서 찾을 수 없습니다.",
                                      extra={'guild_id': guild.id})
                    await asyncio.sleep(0.5)
                    continue
                else:
                    self.logger.info(f"✅ 메시지 ID {message_id}을(를) 성공적으로 가져왔습니다. (서버: {guild.name})",
                                     extra={'guild_id': guild.id})

                existing_emoji_keys = {format_emoji_for_map_key(reaction.emoji) for reaction in message.reactions}

                for emoji_key_in_map in emoji_role_map.keys():
                    if emoji_key_in_map in existing_emoji_keys:
                        self.logger.debug(f"이모지 {emoji_key_in_map}은(는) 메시지 {message_id}에 이미 존재합니다. (서버: {guild.name})",
                                          extra={'guild_id': guild.id})
                        continue
                    try:
                        await message.add_reaction(emoji_key_in_map)
                        self.logger.debug(f"➕ 이모지 {emoji_key_in_map}을(를) 메시지 {message_id}에 추가했습니다. (서버: {guild.name})",
                                          extra={'guild_id': guild.id})
                        await asyncio.sleep(0.5)
                    except discord.HTTPException as e:
                        self.logger.error(
                            f"⛔ 이모지 {emoji_key_in_map}을(를) 메시지 {message_id}에 추가 실패 (서버: {guild.name}): {e}\n{traceback.format_exc()}",
                            extra={'guild_id': guild.id})
                        await asyncio.sleep(0.5)
                    except Exception as e:
                        self.logger.error(
                            f"⛔ 이모지 {emoji_key_in_map}을(를) 메시지 {message_id}에 추가 중 알 수 없는 오류 발생 (서버: {guild.name}): {e}\n{traceback.format_exc()}",
                            extra={'guild_id': guild.id})
                        await asyncio.sleep(0.5)

                await asyncio.sleep(1)

            self.logger.info(f"✅ 리액션 역할 초기화 완료: {guild.name} ({guild.id})", extra={'guild_id': guild.id})

        except Exception as e:
            self.logger.error(f"⛔ 길드 {guild.name} ({guild.id}) 리액션 역할 초기화 중 오류: {e}\n{traceback.format_exc()}",
                              extra={'guild_id': guild.id})

    async def setup_verification_reaction(self, guild: discord.Guild, verification_message_id: int,
                                          verification_emoji: str):
        """Setup verification reaction for a guild"""
        try:
            message = await self.find_message_in_guild(guild, verification_message_id)
            if message:
                if not any(str(r.emoji) == verification_emoji for r in message.reactions):
                    await message.add_reaction(verification_emoji)
                    self.logger.info(
                        f"✅ '{verification_emoji}' 이모지를 인증 메시지 ({verification_message_id})에 추가했습니다. (서버: {guild.name})",
                        extra={'guild_id': guild.id})
        except Exception as e:
            self.logger.error(f"⛔ 인증 이모지 추가 실패 (서버: {guild.name}): {e}\n{traceback.format_exc()}",
                              extra={'guild_id': guild.id})

    async def find_message_in_guild(self, guild: discord.Guild, message_id: int) -> discord.Message:
        """Find a message by ID in any accessible channel of the guild"""
        for channel in guild.text_channels:
            try:
                message = await channel.fetch_message(message_id)
                if message:
                    return message
            except discord.NotFound:
                continue
            except discord.Forbidden:
                self.logger.debug(
                    f"권한 부족으로 채널 #{channel.name} ({channel.id})에서 메시지 {message_id}를 가져올 수 없습니다. (서버: {guild.name})",
                    extra={'guild_id': guild.id})
                continue
            except Exception as e:
                self.logger.error(
                    f"⛔ 메시지 {message_id}를 채널 #{channel.name} ({channel.id})에서 가져오는 중 오류 발생 (서버: {guild.name}): {e}\n{traceback.format_exc()}",
                    extra={'guild_id': guild.id})
                continue
        return None

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent):
        # Debug: Log every reaction event
        guild = self.bot.get_guild(payload.guild_id)
        if not guild:
            return

        self.logger.debug(
            f"Raw reaction add: User {payload.user_id}, Message {payload.message_id}, Emoji {payload.emoji}, Guild {payload.guild_id}",
            extra={'guild_id': guild.id})

        if payload.user_id == self.bot.user.id or (payload.member and payload.member.bot):
            self.logger.debug("Ignoring bot reaction", extra={'guild_id': guild.id})
            return

        if not is_server_configured(guild.id) or not is_feature_enabled(guild.id, 'reaction_roles'):
            return

        # Check for verification reaction first
        verification_message_id = get_server_setting(guild.id, 'verification_message_id')
        verification_emoji = get_server_setting(guild.id, 'verification_emoji', '✅')

        if verification_message_id and payload.message_id == verification_message_id and str(
                payload.emoji) == verification_emoji:
            await self.handle_verification_reaction(payload, guild)
            return

        # Handle regular reaction roles
        await self.handle_reaction_role_add(payload, guild)

    async def handle_verification_reaction(self, payload: discord.RawReactionActionEvent, guild: discord.Guild):
        """Handle verification reaction for a specific guild"""
        self.logger.info(f"Processing verification reaction from user {payload.user_id} in guild {guild.name}",
                         extra={'guild_id': guild.id})

        unverified_role_id = get_role_id(guild.id, 'unverified_role')
        accepted_role_id = get_role_id(guild.id, 'member_role')

        if not unverified_role_id or not accepted_role_id:
            self.logger.error(f"Verification roles not properly configured for guild {guild.name}",
                              extra={'guild_id': guild.id})
            return

        member = payload.member
        if not member:
            try:
                member = await guild.fetch_member(payload.user_id)
            except (discord.NotFound, discord.Forbidden) as e:
                self.logger.error(f"사용자 {payload.user_id}을(를) 가져오는 중 오류 발생 (서버: {guild.name}): {e}",
                                  extra={'guild_id': guild.id})
                return

        unverified_role = guild.get_role(unverified_role_id)
        accepted_role = guild.get_role(accepted_role_id)

        if unverified_role and unverified_role in member.roles:
            try:
                await member.remove_roles(unverified_role, reason="사용자가 인증 완료")
                self.logger.info(
                    f"✅ {member.display_name} ({member.id})님에게서 'UNVERIFIED' 역할을 제거했습니다. (서버: {guild.name})",
                    extra={'guild_id': guild.id})
            except discord.Forbidden:
                self.logger.error(f"⛔ 'UNVERIFIED' 역할 제거 권한이 없습니다. (서버: {guild.name})", extra={'guild_id': guild.id})
            except Exception as e:
                self.logger.error(f"⛔ 'UNVERIFIED' 역할 제거 중 오류 발생 (서버: {guild.name}): {e}", extra={'guild_id': guild.id})

        if accepted_role and accepted_role not in member.roles:
            try:
                await member.add_roles(accepted_role, reason="사용자가 인증 완료")
                self.logger.info(f"✅ {member.display_name} ({member.id})님에게 'ACCEPTED' 역할을 부여했습니다. (서버: {guild.name})",
                                 extra={'guild_id': guild.id})
            except discord.Forbidden:
                self.logger.error(f"⛔ 'ACCEPTED' 역할 부여 권한이 없습니다. (서버: {guild.name})", extra={'guild_id': guild.id})
            except Exception as e:
                self.logger.error(f"⛔ 'ACCEPTED' 역할 부여 중 오류 발생 (서버: {guild.name}): {e}", extra={'guild_id': guild.id})

        # Optionally, remove the user's reaction to clean up
        try:
            message = await guild.get_channel(payload.channel_id).fetch_message(payload.message_id)
            await message.remove_reaction(payload.emoji, member)
        except Exception as e:
            self.logger.warning(f"사용자 리액션 제거 실패 (서버: {guild.name}): {e}", extra={'guild_id': guild.id})

    async def handle_reaction_role_add(self, payload: discord.RawReactionActionEvent, guild: discord.Guild):
        """Handle regular reaction role addition for a specific guild"""
        # Get reaction role mapping for this server
        reaction_role_map = get_reaction_roles(guild.id)

        if payload.message_id not in reaction_role_map:
            self.logger.debug(f"Message {payload.message_id} not in reaction role map for guild {guild.name}",
                              extra={'guild_id': guild.id})
            return

        # Format the emoji key to match the map
        if payload.emoji.id:
            emoji_key = f"<:{payload.emoji.name.lower()}:{payload.emoji.id}>"
        else:
            emoji_key = str(payload.emoji)

        self.logger.debug(f"Looking for emoji key: '{emoji_key}' in message {payload.message_id} (서버: {guild.name})",
                          extra={'guild_id': guild.id})
        self.logger.debug(f"Available keys: {list(reaction_role_map[payload.message_id].keys())}",
                          extra={'guild_id': guild.id})

        role_id = reaction_role_map[payload.message_id].get(emoji_key)

        if not role_id:
            # Try without lowercase for custom emoji (fallback)
            if payload.emoji.id:
                fallback_key = f"<:{payload.emoji.name}:{payload.emoji.id}>"
                role_id = reaction_role_map[payload.message_id].get(fallback_key)
                if role_id:
                    emoji_key = fallback_key
                    self.logger.debug(f"Found role using fallback key: {fallback_key} (서버: {guild.name})",
                                      extra={'guild_id': guild.id})

        if not role_id:
            self.logger.warning(f"메시지 {payload.message_id}에서 알 수 없는 이모지 '{emoji_key}'에 반응 추가됨. (서버: {guild.name})",
                                extra={'guild_id': guild.id})
            self.logger.debug(f"Available emoji keys in map: {list(reaction_role_map[payload.message_id].keys())}",
                              extra={'guild_id': guild.id})
            return

        role = guild.get_role(role_id)
        if not role:
            self.logger.error(f"역할 ID {role_id}을(를) 길드 {guild.name} ({guild.id})에서 찾을 수 없습니다. 설정 확인 필요.",
                              extra={'guild_id': guild.id})
            return

        member = payload.member
        if not member:
            try:
                member = await guild.fetch_member(payload.user_id)
            except discord.NotFound:
                self.logger.warning(f"사용자 ID {payload.user_id}을(를) 찾을 수 없어 역할 추가 실패 (서버: {guild.name}).",
                                    extra={'guild_id': guild.id})
                return
            except discord.Forbidden:
                self.logger.error(f"길드 {guild.name}에서 사용자 {payload.user_id}을(를) 가져올 권한이 없습니다.",
                                  extra={'guild_id': guild.id})
                return
            except Exception as e:
                self.logger.error(
                    f"사용자 {payload.user_id}을(를) 가져오는 중 알 수 없는 오류 발생 (서버: {guild.name}): {e}\n{traceback.format_exc()}",
                    extra={'guild_id': guild.id})
                return

        if member.bot:
            self.logger.debug("Ignoring bot member", extra={'guild_id': guild.id})
            return

        if role in member.roles:
            self.logger.debug(f"사용자 {member.display_name}이(가) 이미 역할 '{role.name}'을(를) 가지고 있습니다. (서버: {guild.name})",
                              extra={'guild_id': guild.id})
            return

        try:
            await member.add_roles(role, reason="Reaction role assigned")
            emoji_log_name = payload.emoji.name if payload.emoji.id else str(payload.emoji)
            self.logger.info(
                f"✅ [리액션 역할] '{role.name}' 역할이 {member.display_name} ({member.id})에게 이모지 '{emoji_log_name}'을(를) 통해 추가되었습니다. (서버: {guild.name})",
                extra={'guild_id': guild.id})
        except discord.Forbidden:
            self.logger.error(
                f"⛔ [리액션 역할] {member.display_name}에게 역할 '{role.name}'을(를) 추가할 권한이 없습니다. (서버: {guild.name})",
                extra={'guild_id': guild.id})
        except discord.HTTPException as e:
            self.logger.error(
                f"⛔ [리액션 역할] 역할 '{role.name}' 추가 중 Discord HTTP 오류 발생 (서버: {guild.name}): {e}\n{traceback.format_exc()}",
                extra={'guild_id': guild.id})
        except Exception as e:
            self.logger.error(f"⛔ [리액션 역할] 역할 추가 실패 (서버: {guild.name}): {e}\n{traceback.format_exc()}",
                              extra={'guild_id': guild.id})

    @commands.Cog.listener()
    async def on_raw_reaction_remove(self, payload: discord.RawReactionActionEvent):
        guild = self.bot.get_guild(payload.guild_id)
        if not guild:
            return

        if payload.user_id == self.bot.user.id:
            return

        if not is_server_configured(guild.id) or not is_feature_enabled(guild.id, 'reaction_roles'):
            return

        # Do not process reaction removals on the verification message
        verification_message_id = get_server_setting(guild.id, 'verification_message_id')
        if verification_message_id and payload.message_id == verification_message_id:
            return

        await self.handle_reaction_role_remove(payload, guild)

    async def handle_reaction_role_remove(self, payload: discord.RawReactionActionEvent, guild: discord.Guild):
        """Handle reaction role removal for a specific guild"""
        # Get reaction role mapping for this server
        reaction_role_map = get_reaction_roles(guild.id)

        if payload.message_id not in reaction_role_map:
            return

        if payload.emoji.id:
            emoji_key = f"<:{payload.emoji.name.lower()}:{payload.emoji.id}>"
        else:
            emoji_key = str(payload.emoji)

        role_id = reaction_role_map[payload.message_id].get(emoji_key)

        # Try fallback for custom emoji if not found
        if not role_id and payload.emoji.id:
            fallback_key = f"<:{payload.emoji.name}:{payload.emoji.id}>"
            role_id = reaction_role_map[payload.message_id].get(fallback_key)

        if not role_id:
            self.logger.debug(f"메시지 {payload.message_id}에서 알 수 없는 이모지 '{emoji_key}' 반응 제거됨. (서버: {guild.name})",
                              extra={'guild_id': guild.id})
            return

        member = None
        try:
            member = await guild.fetch_member(payload.user_id)
        except discord.NotFound:
            self.logger.warning(f"사용자 ID {payload.user_id}을(를) 찾을 수 없어 역할 제거 실패 (서버: {guild.name}).",
                                extra={'guild_id': guild.id})
            return
        except discord.Forbidden:
            self.logger.error(f"길드 {guild.name}에서 사용자 {payload.user_id}을(를) 가져올 권한이 없습니다.",
                              extra={'guild_id': guild.id})
            return
        except Exception as e:
            self.logger.error(
                f"사용자 {payload.user_id}을(를) 가져오는 중 알 수 없는 오류 발생 (서버: {guild.name}): {e}\n{traceback.format_exc()}",
                extra={'guild_id': guild.id})
            return

        if member.bot:
            return

        role = guild.get_role(role_id)
        if not role:
            self.logger.error(f"역할 ID {role_id}을(를) 길드 {guild.name} ({guild.id})에서 찾을 수 없습니다. 설정 확인 필요.",
                              extra={'guild_id': guild.id})
            return

        if role not in member.roles:
            self.logger.debug(f"사용자 {member.display_name}이(가) 역할 '{role.name}'을(를) 가지고 있지 않습니다. (서버: {guild.name})",
                              extra={'guild_id': guild.id})
            return

        try:
            await member.remove_roles(role, reason="Reaction role removed")
            emoji_log_name = payload.emoji.name if payload.emoji.id else str(payload.emoji)
            self.logger.info(
                f"➖ [리액션 역할] '{role.name}' 역할이 {member.display_name} ({member.id})에게서 이모지 '{emoji_log_name}'을(를) 통해 제거되었습니다. (서버: {guild.name})",
                extra={'guild_id': guild.id})
        except discord.Forbidden:
            self.logger.error(
                f"⛔ [리액션 역할] {member.display_name}에게서 역할 '{role.name}'을(를) 제거할 권한이 없습니다. (서버: {guild.name})",
                extra={'guild_id': guild.id})
        except discord.HTTPException as e:
            self.logger.error(
                f"⛔ [리액션 역할] 역할 '{role.name}' 제거 중 Discord HTTP 오류 발생 (서버: {guild.name}): {e}\n{traceback.format_exc()}",
                extra={'guild_id': guild.id})
        except Exception as e:
            self.logger.error(f"⛔ [리액션 역할] 역할 제거 실패 (서버: {guild.name}): {e}\n{traceback.format_exc()}",
                              extra={'guild_id': guild.id})


async def setup(bot):
    cog = ReactionRoles(bot)
    await bot.add_cog(cog)
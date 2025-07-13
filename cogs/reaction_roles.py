import discord
from discord.ext import commands
import traceback
import asyncio

from utils import config
from utils.logger import get_logger
from utils.config import REACTION_ROLE_MAP


class ReactionRoles(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

        self.logger = get_logger(
            "리액션 역할",
            bot=self.bot,
            discord_log_channel_id=config.LOG_CHANNEL_ID
        )

        self.guild_id = config.GUILD_ID
        self.logger.info("ReactionRoles Cog 초기화 완료.")

        self.reaction_role_map = REACTION_ROLE_MAP

    async def populate_reactions(self):
        guild = self.bot.get_guild(self.guild_id)
        if not guild:
            self.logger.error(f"❌ 길드 ID {self.guild_id}을(를) 찾을 수 없습니다. ReactionRoles 기능이 작동하지 않습니다.")
            return

        def format_emoji_for_map_key(e):
            """Formats a discord.Emoji or reaction.emoji into the string key used in reaction_role_map."""
            if isinstance(e, str):
                return e
            return f"<:{e.name}:{e.id}>" if getattr(e, "id", None) else str(e)

        for message_id, emoji_role_map in self.reaction_role_map.items():
            message = None
            found_channel = None

            for channel in guild.text_channels:
                try:
                    message = await channel.fetch_message(message_id)
                    if message:
                        found_channel = channel
                        break
                except discord.NotFound:
                    continue
                except discord.Forbidden:
                    self.logger.debug(f"권한 부족으로 채널 #{channel.name} ({channel.id})에서 메시지 {message_id}를 가져올 수 없습니다.")
                    continue
                except Exception as e:
                    self.logger.error(
                        f"❌ 메시지 {message_id}를 채널 #{channel.name} ({channel.id})에서 가져오는 중 오류 발생: {e}\n{traceback.format_exc()}")
                    continue

            if not message:
                self.logger.error(f"❌ 메시지 ID {message_id}을(를) 접근 가능한 어떤 채널에서도 찾을 수 없습니다. 리액션 역할이 제대로 작동하지 않을 수 있습니다.")
                await asyncio.sleep(0.5)
                continue
            else:
                self.logger.info(f"✅ 메시지 ID {message_id} ({message.jump_url})을(를) 성공적으로 가져왔습니다.")

            existing_emoji_keys = {format_emoji_for_map_key(reaction.emoji) for reaction in message.reactions}

            for emoji_key_in_map in emoji_role_map.keys():
                if emoji_key_in_map in existing_emoji_keys:
                    self.logger.debug(f"이모지 {emoji_key_in_map}은(는) 메시지 {message_id}에 이미 존재합니다.")
                    continue
                try:
                    await message.add_reaction(emoji_key_in_map)
                    self.logger.info(f"➕ 이모지 {emoji_key_in_map}을(를) 메시지 {message_id}에 추가했습니다.")
                    await asyncio.sleep(0.5)
                except discord.HTTPException as e:
                    self.logger.error(
                        f"❌ 이모지 {emoji_key_in_map}을(를) 메시지 {message_id}에 추가 실패: {e} (권한 또는 이모지 오류?)\n{traceback.format_exc()}")
                    await asyncio.sleep(0.5)
                except Exception as e:
                    self.logger.error(
                        f"❌ 이모지 {emoji_key_in_map}을(를) 메시지 {message_id}에 추가 중 알 수 없는 오류 발생: {e}\n{traceback.format_exc()}")
                    await asyncio.sleep(0.5)

            await asyncio.sleep(1)

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent):
        if payload.user_id == self.bot.user.id or payload.member and payload.member.bot:
            return

        if payload.message_id not in self.reaction_role_map:
            return

        emoji_key = f"<:{payload.emoji.name}:{payload.emoji.id}>" if payload.emoji.id else str(payload.emoji)
        role_id = self.reaction_role_map[payload.message_id].get(emoji_key)

        if not role_id:
            self.logger.debug(f"메시지 {payload.message_id}에서 알 수 없는 이모지 '{emoji_key}'에 반응 추가됨. 무시.")
            return

        guild = self.bot.get_guild(payload.guild_id)
        if not guild:
            self.logger.warning(f"길드 ID {payload.guild_id}을(를) 찾을 수 없어 역할 추가 실패.")
            return

        role = guild.get_role(role_id)
        if not role:
            self.logger.error(f"역할 ID {role_id}을(를) 길드 {guild.name} ({guild.id})에서 찾을 수 없습니다. 설정 확인 필요.")
            return

        member = payload.member
        if not member:
            try:
                member = await guild.fetch_member(payload.user_id)
            except discord.NotFound:
                self.logger.warning(f"사용자 ID {payload.user_id}을(를) 찾을 수 없어 역할 추가 실패 (아마도 서버를 떠났을 수 있음).")
                return
            except discord.Forbidden:
                self.logger.error(f"길드 {guild.name}에서 사용자 {payload.user_id}을(를) 가져올 권한이 없습니다.")
                return
            except Exception as e:
                self.logger.error(f"사용자 {payload.user_id}을(를) 가져오는 중 알 수 없는 오류 발생: {e}\n{traceback.format_exc()}")
                return

        if member.bot:
            return

        if role in member.roles:
            self.logger.debug(f"사용자 {member.display_name}이(가) 이미 역할 '{role.name}'을(를) 가지고 있습니다. 무시.")
            return

        try:
            await member.add_roles(role, reason="Reaction role assigned")
            emoji_log_name = payload.emoji.name if payload.emoji.id else str(payload.emoji)
            self.logger.info(
                f"✅ [리액션 역할] '{role.name}' 역할이 {member.display_name} ({member.id})에게 이모지 '{emoji_log_name}'을(를) 통해 추가되었습니다.")
        except discord.Forbidden:
            self.logger.error(f"❌ [리액션 역할] {member.display_name}에게 역할 '{role.name}'을(를) 추가할 권한이 없습니다. 봇 권한을 확인해주세요.")
        except discord.HTTPException as e:
            self.logger.error(f"❌ [리액션 역할] 역할 '{role.name}' 추가 중 Discord HTTP 오류 발생: {e}\n{traceback.format_exc()}")
        except Exception as e:
            self.logger.error(f"❌ [리액션 역할] 역할 추가 실패: {e}\n{traceback.format_exc()}")

    @commands.Cog.listener()
    async def on_raw_reaction_remove(self, payload: discord.RawReactionActionEvent):
        if payload.user_id == self.bot.user.id:
            return

        if payload.message_id not in self.reaction_role_map:
            return

        emoji_key = f"<:{payload.emoji.name}:{payload.emoji.id}>" if payload.emoji.id else str(payload.emoji)
        role_id = self.reaction_role_map[payload.message_id].get(emoji_key)

        if not role_id:
            self.logger.debug(f"메시지 {payload.message_id}에서 알 수 없는 이모지 '{emoji_key}' 반응 제거됨. 무시.")
            return

        guild = self.bot.get_guild(payload.guild_id)
        if not guild:
            self.logger.warning(f"길드 ID {payload.guild_id}을(를) 찾을 수 없어 역할 제거 실패.")
            return

        member = None
        try:
            member = await guild.fetch_member(payload.user_id)
        except discord.NotFound:
            self.logger.warning(f"사용자 ID {payload.user_id}을(를) 찾을 수 없어 역할 제거 실패 (아마도 서버를 떠났을 수 있음).")
            return
        except discord.Forbidden:
            self.logger.error(f"길드 {guild.name}에서 사용자 {payload.user_id}을(를) 가져올 권한이 없습니다.")
            return
        except Exception as e:
            self.logger.error(f"사용자 {payload.user_id}을(를) 가져오는 중 알 수 없는 오류 발생: {e}\n{traceback.format_exc()}")
            return

        if member.bot:
            return

        role = guild.get_role(role_id)
        if not role:
            self.logger.error(f"역할 ID {role_id}을(를) 길드 {guild.name} ({guild.id})에서 찾을 수 없습니다. 설정 확인 필요.")
            return

        if role not in member.roles:
            self.logger.debug(f"사용자 {member.display_name}이(가) 역할 '{role.name}'을(를) 가지고 있지 않습니다. 무시.")
            return

        try:
            await member.remove_roles(role, reason="Reaction role removed")
            emoji_log_name = payload.emoji.name if payload.emoji.id else str(payload.emoji)
            self.logger.info(
                f"❎ [리액션 역할] '{role.name}' 역할이 {member.display_name} ({member.id})에게서 이모지 '{emoji_log_name}'을(를) 통해 제거되었습니다.")
        except discord.Forbidden:
            self.logger.error(f"❌ [리액션 역할] {member.display_name}에게서 역할 '{role.name}'을(를) 제거할 권한이 없습니다. 봇 권한을 확인해주세요.")
        except discord.HTTPException as e:
            self.logger.error(f"❌ [리액션 역할] 역할 '{role.name}' 제거 중 Discord HTTP 오류 발생: {e}\n{traceback.format_exc()}")
        except Exception as e:
            self.logger.error(f"❌ [리액션 역할] 역할 제거 실패: {e}\n{traceback.format_exc()}")


async def setup(bot):
    await bot.add_cog(ReactionRoles(bot))
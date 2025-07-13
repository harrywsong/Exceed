import discord
from discord.ext import commands
import traceback
import asyncio
import re  # Import re for custom emoji parsing

from utils import config
from utils.logger import get_logger


# from utils.config import REACTION_ROLE_MAP # REMOVED: No longer using static map

class ReactionRoles(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.guild_id = config.GUILD_ID
        # self.reaction_role_map is no longer needed as data comes from DB
        self.logger = get_logger(
            "리액션 역할",
            bot=self.bot,
            discord_log_channel_id=config.LOG_CHANNEL_ID
        )
        self.logger.info("ReactionRoles Cog 초기화 완료.")

        # Schedule population after bot is fully ready
        self.bot.loop.create_task(self.wait_until_ready_then_populate())

    async def wait_until_ready_then_populate(self):
        await self.bot.wait_until_ready()
        try:
            await self.populate_reactions()
        except Exception as e:
            self.logger.error(f"❌ ReactionRoles 초기화 중 오류 발생: {e}\n{traceback.format_exc()}")

    async def populate_reactions(self):
        """
        Fetches reaction role entries from the database and ensures reactions are added to messages.
        This function is crucial for initial setup and re-syncing reactions on messages.
        """
        self.logger.info("리액션 역할 동기화 시작 (데이터베이스에서 가져오기).")
        guild = self.bot.get_guild(self.guild_id)
        if not guild:
            self.logger.error(f"❌ 길드 ID {self.guild_id}을(를) 찾을 수 없습니다. ReactionRoles 기능이 작동하지 않습니다.")
            return

        db_entries = await self.get_all_reaction_role_entries_db()
        if not db_entries:
            self.logger.info("데이터베이스에 리액션 역할 항목이 없습니다. 건너뜁니다.")
            return

        # Group entries by message_id and channel_id
        messages_to_populate = {}
        for entry in db_entries:
            message_id = entry['message_id']
            channel_id = entry['channel_id']
            emoji = entry['emoji']
            role_id = entry['role_id']

            if (message_id, channel_id) not in messages_to_populate:
                messages_to_populate[(message_id, channel_id)] = {'emojis': {}}
            messages_to_populate[(message_id, channel_id)]['emojis'][emoji] = role_id

        for (message_id, channel_id), data in messages_to_populate.items():
            emoji_role_map = data['emojis']

            channel = guild.get_channel(channel_id)
            if not channel:
                self.logger.warning(f"⚠️ 채널 ID {channel_id}을(를) 찾을 수 없습니다. 메시지 {message_id}의 리액션 역할 건너뜁니다.")
                continue

            try:
                message = await channel.fetch_message(message_id)
                self.logger.info(f"✅ 메시지 ID {message_id} ({message.jump_url})을(를) 성공적으로 가져왔습니다.")

                # Get existing reactions by the bot
                bot_reacted_emojis = set()
                for reaction in message.reactions:
                    if reaction.me:  # Check if the bot itself reacted
                        if isinstance(reaction.emoji, str):  # Unicode emoji
                            bot_reacted_emojis.add(reaction.emoji)
                        elif reaction.emoji.id:  # Custom emoji
                            # Format custom emoji as <:name:id> for consistent comparison
                            bot_reacted_emojis.add(f"<:{reaction.emoji.name}:{reaction.emoji.id}>")

                # Add reactions if they are not already present by the bot
                for emoji_str in emoji_role_map.keys():
                    if emoji_str in bot_reacted_emojis:
                        self.logger.debug(f"ℹ️ 이모지 {emoji_str}이(가) 메시지 {message_id}에 이미 있습니다. 건너뜁니다.")
                        continue

                    try:
                        # Discord.py handles unicode and custom emoji strings directly
                        await message.add_reaction(emoji_str)
                        self.logger.info(f"➕ 이모지 {emoji_str}을(를) 메시지 {message_id}에 추가했습니다.")
                        await asyncio.sleep(0.7)  # Delay to respect Discord's rate limits
                    except discord.Forbidden:
                        self.logger.error(f"❌ 메시지 {message_id}에 이모지 {emoji_str}을(를) 추가할 권한이 없습니다.")
                    except discord.HTTPException as e:
                        self.logger.error(f"❌ 이모지 {emoji_str}을(를) 메시지 {message_id}에 추가 중 HTTP 오류 발생: {e}")
                    except Exception as e:
                        self.logger.error(f"❌ 이모지 {emoji_str}을(를) 메시지 {message_id}에 추가 중 알 수 없는 오류 발생: {e}")

            except discord.NotFound:
                self.logger.warning(f"⚠️ 메시지 ID {message_id}을(를) 찾을 수 없습니다. 데이터베이스에서 제거를 고려하세요.")
                # Optionally, remove from DB if message is not found
                # await self.remove_reaction_role_entry_db(message_id, None, remove_all_emojis=True)
            except discord.Forbidden:
                self.logger.error(f"❌ 메시지 ID {message_id}을(를) 가져올 권한이 없습니다.")
            except Exception as e:
                self.logger.error(f"❌ 메시지 {message_id}의 리액션 역할 설정 중 오류 발생: {e}\n{traceback.format_exc()}")

            await asyncio.sleep(1)  # Delay between processing messages
        self.logger.info("리액션 역할 동기화 완료.")

    async def get_all_reaction_role_entries_db(self):
        """Fetches all reaction role entries from the database."""
        if not self.bot.pool:
            self.logger.error("데이터베이스 풀이 초기화되지 않았습니다.")
            return []
        async with self.bot.pool.acquire() as conn:
            records = await conn.fetch("SELECT message_id, channel_id, emoji, role_id FROM reaction_role_entries")
            return [dict(r) for r in records]

    async def add_reaction_role_entry_db(self, message_id: int, channel_id: int, emoji: str, role_id: int):
        """Adds a new reaction role entry to the database and attempts to add the reaction to the message."""
        if not self.bot.pool:
            raise RuntimeError("데이터베이스 풀이 초기화되지 않았습니다.")
        async with self.bot.pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO reaction_role_entries (message_id, channel_id, emoji, role_id)
                VALUES ($1, $2, $3, $4) ON CONFLICT (message_id, emoji) DO
                UPDATE SET role_id = EXCLUDED.role_id, channel_id = EXCLUDED.channel_id
                """,
                message_id, channel_id, emoji, role_id
            )
            self.logger.info(f"데이터베이스에 리액션 역할 추가/업데이트됨: 메시지={message_id}, 이모지={emoji}, 역할={role_id}")

            # Attempt to add the reaction to the Discord message immediately
            guild = self.bot.get_guild(self.guild_id)
            if guild:
                channel = guild.get_channel(channel_id)
                if channel:
                    try:
                        message = await channel.fetch_message(message_id)
                        await message.add_reaction(emoji)
                        self.logger.info(f"메시지 {message_id}에 이모지 {emoji} 추가됨.")
                    except discord.NotFound:
                        self.logger.warning(f"메시지 {message_id}를 찾을 수 없어 이모지 {emoji}를 추가할 수 없습니다.")
                    except discord.Forbidden:
                        self.logger.error(f"메시지 {message_id}에 이모지 {emoji}를 추가할 권한이 없습니다.")
                    except Exception as e:
                        self.logger.error(f"메시지 {message_id}에 이모지 {emoji}를 추가 중 오류 발생: {e}")
            # Re-run populate_reactions to ensure consistency across all messages
            await self.populate_reactions()

    async def remove_reaction_role_entry_db(self, message_id: int, emoji: str):
        """Removes a reaction role entry from the database and attempts to remove the reaction from the message."""
        if not self.bot.pool:
            raise RuntimeError("데이터베이스 풀이 초기화되지 않았습니다.")
        async with self.bot.pool.acquire() as conn:
            # First, fetch channel_id before deleting the entry
            channel_id_row = await conn.fetchrow(
                "SELECT channel_id FROM reaction_role_entries WHERE message_id = $1 AND emoji = $2",
                message_id, emoji
            )

            result = await conn.execute(
                "DELETE FROM reaction_role_entries WHERE message_id = $1 AND emoji = $2",
                message_id, emoji
            )
            if result == "DELETE 1":
                self.logger.info(f"데이터베이스에서 리액션 역할 제거됨: 메시지={message_id}, 이모지={emoji}")

                # Attempt to remove the reaction from the Discord message immediately
                if channel_id_row:
                    guild = self.bot.get_guild(self.guild_id)
                    if guild:
                        channel = guild.get_channel(channel_id_row['channel_id'])
                        if channel:
                            try:
                                message = await channel.fetch_message(message_id)
                                # Remove only bot's reaction
                                await message.remove_reaction(emoji, self.bot.user)
                                self.logger.info(f"메시지 {message_id}에서 이모지 {emoji} 제거됨.")
                            except discord.NotFound:
                                self.logger.warning(f"메시지 {message_id}를 찾을 수 없어 이모지 {emoji}를 제거할 수 없습니다.")
                            except discord.Forbidden:
                                self.logger.error(f"메시지 {message_id}에서 이모지 {emoji}를 제거할 권한이 없습니다.")
                            except Exception as e:
                                self.logger.error(f"메시지 {message_id}에서 이모지 {emoji}를 제거 중 오류 발생: {e}")
                # Re-run populate_reactions to ensure consistency across all messages
                await self.populate_reactions()
                return True
            else:
                self.logger.warning(f"데이터베이스에서 리액션 역할 제거 실패 (찾을 수 없음): 메시지={message_id}, 이모지={emoji}")
                return False

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent):
        if payload.guild_id is None or payload.guild_id != self.guild_id:
            return  # Ignore DMs or other guilds

        if payload.user_id == self.bot.user.id:
            return  # Ignore bot's own reactions

        guild = self.bot.get_guild(payload.guild_id)
        if not guild:
            self.logger.warning(f"길드 ID {payload.guild_id}을(를) 찾을 수 없어 역할 추가 실패.")
            return

        # Format emoji string for database lookup (custom emoji or unicode)
        emoji_key = str(payload.emoji)  # Discord.py's str(emoji) handles both unicode and custom <:name:id>

        # Fetch the reaction role mapping from the database
        async with self.bot.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT role_id FROM reaction_role_entries WHERE message_id = $1 AND emoji = $2",
                payload.message_id, emoji_key
            )
            if not row:
                self.logger.debug(f"메시지 {payload.message_id} 및 이모지 {emoji_key}에 대한 리액션 역할 매핑을 찾을 수 없습니다.")
                return

            role_id = row['role_id']

        try:
            member = guild.get_member(payload.user_id)
            if member is None:
                member = await guild.fetch_member(payload.user_id)  # Try fetching if not in cache
        except discord.NotFound:
            self.logger.warning(f"사용자 {payload.user_id}을(를) 길드 {guild.name}에서 찾을 수 없어 역할 추가 실패 (아마도 서버를 떠났을 수 있음).")
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

        if payload.guild_id is None or payload.guild_id != self.guild_id:
            return  # Ignore DMs or other guilds

        guild = self.bot.get_guild(payload.guild_id)
        if not guild:
            self.logger.warning(f"길드 ID {payload.guild_id}을(를) 찾을 수 없어 역할 제거 실패.")
            return

        # Format emoji string for database lookup (custom emoji or unicode)
        emoji_key = str(payload.emoji)

        # Fetch the reaction role mapping from the database
        async with self.bot.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT role_id FROM reaction_role_entries WHERE message_id = $1 AND emoji = $2",
                payload.message_id, emoji_key
            )
            if not row:
                self.logger.debug(f"메시지 {payload.message_id} 및 이모지 {emoji_key}에 대한 리액션 역할 매핑을 찾을 수 없습니다.")
                return

            role_id = row['role_id']

        try:
            member = guild.get_member(payload.user_id)
            if member is None:
                member = await guild.fetch_member(payload.user_id)  # Try fetching if not in cache
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
    cog = ReactionRoles(bot)
    await bot.add_cog(cog)


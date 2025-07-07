import discord
from discord.ext import commands
import logging

class ReactionRoles(commands.Cog):
    def __init__(self, bot, logger, guild_id):
        self.bot = bot
        self.logger = logger
        self.guild_id = guild_id

        # message_id: {emoji: role_id}
        self.reaction_role_map = {
            1391796467060178984: {  # Message 1
                "🇼": 1391799163624100083,
                "🇨": 1391799188165234688,
                "🇪": 1391799211145822238,
            },
            1391800751856291870: {  # Message 2
                "<:valoradiant:1390960105494810684>": 1391799337633448097,
                "<:valoimmortal:1390960097483690044>": 1391799428473688104,
                "<:valoascendant:1390960089241878559>": 1391799445691039874,
                "<:valodiamond:1390960077262815252>": 1391799520651776030,
                "<:valoplatinum:1390960067502936074>": 1391799537261351034,
                "<:valogold:1390960054470971492>": 1391799554638221456,
                "<:valosilver:1390960044509761617>": 1391799566889648179,
                "<:valobronze:1390960028466282579>": 1391799579036352513,
                "<:valoiron:1390960012993626233>": 1391799599336919233,
                "<:valounranked:1390960604537290874>": 1391799825858564156,
            },
            1391803147080568974: {  # Message 3
                "<:valoduelist:1390960470789193859>": 1391806841708613652,
                "<:valoinitiator:1390960452854485102>": 1391806880401326080,
                "<:valosentinel:1390960437805453502>": 1391806901569716355,
                "<:valocontroller:1390960462455111782>": 1391806916640112660,
            },
            1391805023473762438: {  # Message 4
                "<:valopremier:1391803981864374414>": 1391804435918749777,
            },
        }

    @commands.Cog.listener()
    async def on_ready(self):
        self.logger.info("ReactionRoles 코그가 준비되었습니다. 이모지 반응을 추가하는 중...")
        await self.populate_reactions()

    async def populate_reactions(self):
        guild = self.bot.get_guild(self.guild_id)
        if not guild:
            self.logger.error(f"Guild with ID {self.guild_id} not found!")
            return

        def format_emoji(e):
            if isinstance(e, str):
                return e
            return f"<:{e.name}:{e.id}>" if e.id else str(e)

        for message_id, emoji_role_map in self.reaction_role_map.items():
            message = None

            for channel in guild.text_channels:
                try:
                    message = await channel.fetch_message(message_id)
                    if message:
                        break
                except discord.NotFound:
                    continue
                except discord.Forbidden:
                    self.logger.warning(f"No permission to fetch messages in #{channel.name}")
                    continue
                except Exception as e:
                    self.logger.error(f"Error fetching message {message_id} in #{channel.name}: {e}")
                    continue

            if not message:
                self.logger.error(f"Could not find message with ID {message_id} in any accessible channel.")
                continue

            existing_emojis = [format_emoji(reaction.emoji) for reaction in message.reactions]

            for emoji in emoji_role_map.keys():
                if emoji in existing_emojis:
                    continue  # Already reacted with this emoji
                try:
                    await message.add_reaction(emoji)
                    self.logger.info(f"Added emoji {emoji} to message {message_id}.")
                except Exception as e:
                    self.logger.error(f"Failed to add emoji {emoji} to message {message_id}: {e}")

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent):
        if payload.message_id not in self.reaction_role_map:
            return

        emoji_key = (
            f"<:{payload.emoji.name}:{payload.emoji.id}>" if payload.emoji.id else str(payload.emoji)
        )
        role_id = self.reaction_role_map[payload.message_id].get(emoji_key)

        if not role_id:
            return

        guild = self.bot.get_guild(payload.guild_id)
        if not guild:
            return

        role = guild.get_role(role_id)
        if not role:
            return

        member = payload.member or await guild.fetch_member(payload.user_id)
        if not member:
            return

        if member.bot:  # Skip bots
            return

        try:
            await member.add_roles(role, reason="반응 역할 부여")
            emoji_log = payload.emoji.name if payload.emoji.id else str(payload.emoji)
            self.logger.info(
                f"[ReactionRole] ✅ {member.display_name}님에게 '{emoji_log}' 이모지로 역할 '{role.name}'을(를) 부여했습니다.")
        except Exception as e:
            self.logger.error(f"[ReactionRole] 역할 부여 실패: {e}")

    @commands.Cog.listener()
    async def on_raw_reaction_remove(self, payload: discord.RawReactionActionEvent):
        if payload.message_id not in self.reaction_role_map:
            return

        emoji_key = (
            f"<:{payload.emoji.name}:{payload.emoji.id}>" if payload.emoji.id else str(payload.emoji)
        )
        role_id = self.reaction_role_map[payload.message_id].get(emoji_key)

        if not role_id:
            return

        guild = self.bot.get_guild(payload.guild_id)
        if not guild:
            return

        member = await guild.fetch_member(payload.user_id)
        if not member:
            return

        if member.bot:  # Skip bots
            return

        role = guild.get_role(role_id)
        if not role:
            return

        try:
            await member.remove_roles(role, reason="반응 역할 제거")
            emoji_log = payload.emoji.name if payload.emoji.id else str(payload.emoji)
            self.logger.info(
                f"[ReactionRole] ❎ {member.display_name}님에게서 '{emoji_log}' 이모지로 역할 '{role.name}'을(를) 제거했습니다.")
        except Exception as e:
            self.logger.error(f"[ReactionRole] 역할 제거 실패: {e}")

async def setup(bot):
    from utils.logger import get_logger
    # Set your guild ID and Discord log channel ID here:
    GUILD_ID = 1389527318699053178  # replace with your guild ID
    DISCORD_LOG_CHANNEL_ID = 1389739434110484612  # replace with your log channel ID

    logger = get_logger("bot", bot=bot, discord_log_channel_id=DISCORD_LOG_CHANNEL_ID)
    await bot.add_cog(ReactionRoles(bot, logger, GUILD_ID))

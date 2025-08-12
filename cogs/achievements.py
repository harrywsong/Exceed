import discord
from discord.ext import commands, tasks
from discord import app_commands
import json
import os
from collections import defaultdict
import datetime
from datetime import timedelta
import asyncio
import pytz
import traceback
from typing import Optional

from utils.config import ACHIEVEMENT_DATA_PATH, GHOST_HUNTER_ID, HOLIDAYS, ACHIEVEMENT_CHANNEL_ID, \
    ACHIEVEMENT_ALERT_CHANNEL_ID, GUILD_ID, \
    ACHIEVEMENT_EMOJIS

LOCAL_SERVER_TZ = pytz.timezone("US/Eastern")


class PersistentAchievementView(discord.ui.View):
    def __init__(self, bot):
        super().__init__(timeout=None)
        self.bot = bot
        self.current_page = 0
        self.max_pages = 0

    async def _get_data(self):
        cog = self.bot.get_cog("Achievements")
        if not cog:
            return None, None

        members = await cog._get_sorted_members()
        self.max_pages = len(members) - 1 if members else 0
        self.update_buttons()
        return cog, members

    def update_buttons(self):
        self.first.disabled = self.current_page == 0
        self.prev_5.disabled = self.current_page == 0
        self.prev.disabled = self.current_page == 0
        self.next.disabled = self.current_page == self.max_pages
        self.next_5.disabled = self.current_page == self.max_pages
        self.last.disabled = self.current_page == self.max_pages

    async def get_current_embed(self, cog, members):
        if not members:
            return discord.Embed(description="No members found with achievements.")

        current_member = members[self.current_page]
        return await cog._create_achievements_embed(current_member, self.current_page + 1, self.max_pages + 1)

    async def update_response(self, interaction: discord.Interaction):
        cog, members = await self._get_data()
        if not cog or not members:
            await interaction.response.edit_message(content="An error occurred while fetching achievement data.",
                                                    view=None)
            return

        embed = await self.get_current_embed(cog, members)
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="« First", style=discord.ButtonStyle.blurple, custom_id="persistent_first_page_button")
    async def first(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.current_page = 0
        await self.update_response(interaction)

    @discord.ui.button(label="« 5", style=discord.ButtonStyle.secondary, custom_id="persistent_prev_5_button")
    async def prev_5(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.current_page = max(0, self.current_page - 5)
        await self.update_response(interaction)

    @discord.ui.button(label="‹ Prev", style=discord.ButtonStyle.secondary, custom_id="persistent_prev_page_button")
    async def prev(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.current_page = max(0, self.current_page - 1)
        await self.update_response(interaction)

    @discord.ui.button(label="Next ›", style=discord.ButtonStyle.secondary, custom_id="persistent_next_page_button")
    async def next(self, interaction: discord.Interaction, button: discord.ui.Button):
        cog, members = await self._get_data()
        if not members:
            await interaction.response.edit_message(content="An error occurred while fetching achievement data.",
                                                    view=None)
            return
        self.current_page = min(len(members) - 1, self.current_page + 1)
        await self.update_response(interaction)

    @discord.ui.button(label="5 »", style=discord.ButtonStyle.secondary, custom_id="persistent_next_5_button")
    async def next_5(self, interaction: discord.Interaction, button: discord.ui.Button):
        cog, members = await self._get_data()
        if not members:
            await interaction.response.edit_message(content="An error occurred while fetching achievement data.",
                                                    view=None)
            return
        self.current_page = min(len(members) - 1, self.current_page + 5)
        await self.update_response(interaction)

    @discord.ui.button(label="Last »", style=discord.ButtonStyle.blurple, custom_id="persistent_last_page_button")
    async def last(self, interaction: discord.Interaction, button: discord.ui.Button):
        cog, members = await self._get_data()
        if not members:
            await interaction.response.edit_message(content="An error occurred while fetching achievement data.",
                                                    view=None)
            return
        self.current_page = len(members) - 1
        await self.update_response(interaction)

    async def post_achievements_display(self):
        channel = self.bot.get_channel(ACHIEVEMENT_CHANNEL_ID)
        if not channel:
            print(f"Error: Leaderboard channel with ID {ACHIEVEMENT_CHANNEL_ID} not found.")
            return

        try:
            # 이전 메시지를 삭제합니다.
            async for message in channel.history(limit=50):
                if message.author == self.bot.user and message.embeds and (
                        "업적 현황" in message.embeds[0].title or "업적 목록 및 힌트" in message.embeds[0].title
                ):
                    try:
                        await message.delete()
                        print(f"이전 업적 메시지 삭제 완료 (ID: {message.id}).")
                    except discord.Forbidden:
                        print("삭제 권한이 없습니다.")
                    except discord.NotFound:
                        print("메시지를 찾을 수 없어 삭제를 건너뜁니다.")

            # 새로운 임베드를 생성하고 지속적인 뷰와 함께 게시합니다.
            cog = self.bot.get_cog("Achievements")
            if not cog:
                print("Achievements cog not found.")
                return

            members = await self._get_sorted_members()
            if members:
                # 뷰 객체 생성 시 봇 인스턴스만 전달합니다.
                view = PersistentAchievementView(self.bot)

                # 뷰의 get_current_embed 메서드를 사용하여 초기 임베드를 가져옵니다.
                initial_embed = await view.get_current_embed(cog, members)

                await channel.send(embed=initial_embed, view=view)
            else:
                await channel.send(embed=discord.Embed(description="No members found with achievements."))

        except Exception as e:
            print(f"업적 메시지 생성 및 전송 실패: {e}")
            traceback.print_exc()
class Achievements(commands.Cog):
    GENERAL_ACHIEVEMENTS = {
        "🎯 Achievement Hunter": "10개의 일반 업적을 달성하세요.",
        "🦋 Social Butterfly I": "100개의 메시지를 작성하세요.",
        "🦋 Social Butterfly II": "500개의 메시지를 작성하세요.",
        "🦋 Social Butterfly III": "1000개의 메시지를 작성하세요.",
        "🗺️ Explorer": "10개의 다른 채널에서 메시지를 작성하세요.",
        "😂 Meme Maker": "50개의 첨부 파일 또는 임베드 메시지를 보내세요.",
        "📚 Knowledge Keeper": "20개의 링크를 공유하세요.",
        "🎄 Holiday Greeter": "5개의 다른 공휴일에 메시지를 보내세요.",
        "🦉 Night Owl": "새벽 5시에서 6시 사이에 메시지를 보내세요.",
        "🐦 Early Bird": "오전 9시에서 10시 사이에 메시지를 보내세요.",
        "🗓️ Daily Devotee": "7일 연속으로 메시지를 보내세요.",
        "⚔️ Weekend Warrior": "10번의 주말에 메시지를 보내세요.",
        "🎂 First Anniversary": "봇과 함께한 1주년을 맞이하세요.",
        "🎖️ Veteran": "서버에 가입한 지 365일이 지나고 메시지를 보내세요.",
        "✨ Boost Buddy": "서버를 부스팅하세요.",
        "🎨 The Collector": "10개의 다른 이모티콘으로 반응하세요.",
        "💬 Reaction Responder": "50개의 다른 메시지에 반응하세요.",
        "👣 First Steps": "첫 번째 명령어를 사용하세요.",
        "🤖 Bot Buddy": "100번 봇과 상호작용하세요.",
        "🗣️ Voice Veteran": "음성 채널에 10시간 동안 접속하세요.",
        "🎧 Loyal Listener": "음성 채널에 50시간 동안 접속하세요."
    }

    HIDDEN_ACHIEVEMENTS = {
        "🤫 The Echo": "봇에게 특별한 한 마디를 속삭이면, 그 말이 메아리가 되어 돌아옵니다.",
        "🕛 Midnight Mystery": "하루가 끝나고 새로운 하루가 시작될 때, 조용히 나타나는 현상을 목격하세요.",
        "🪐 Zero Gravity": "무중력 상태에서는 오직 당신의 목소리만 울려 퍼집니다.",
        "⏳ Time Capsule": "아주 오래된 추억을 되살려보세요.",
        "🔄 Palindrome Pro": "말장난은 거꾸로 해도 통합니다.",
        "🤐 The Unmentionable": "모두가 알지만 누구도 입 밖에 내지 않는, 그런 단어가 존재합니다.",
        "🙉 I'm Not Listening": "특정 단어에 대한 경고를 무시하고 자유롭게 외쳐보세요.",
        "❄️ Code Breaker": "차가운 겨울을 상징하는 단 하나의 무엇이 모든 것을 바꿔놓을 수 있습니다.",
        "👻 Ghost Hunter": "서버에 없는 유령을 찾아 이름을 불러보세요.",
        "✒️ Invisible Ink": "아무도 볼 수 없는 비밀 메시지를 만들어보세요.",
        "📢 Echo Chamber": "연속된 외침이 만들어내는 소리, 그 메아리를 들어보세요.",
        "🚶 Shadow Lurker": "그림자 속에 숨어 있다가 빛 속으로 걸어 나오세요.",
        "✍️ Phantom Poster": "당신의 메시지는 유령처럼 재빨리 모습을 바꿉니다. 아무도 그 변화를 눈치채지 못하게 해보세요.",
        "❤️ Secret Admirer": "봇의 마음에 불을 붙여보세요.",
        "🔍 Error 404": "존재하지 않는 페이지를 찾아 헤매는 것처럼 명령어를 입력해보세요.",
        "🔔 Ping Master": "봇에게 당신의 존재를 알리세요."
    }

    ACHIEVEMENT_EMOJI_MAP = {
        "Achievement Hunter": "🎯",
        "Social Butterfly I": "🦋",
        "Social Butterfly II": "🦋",
        "Social Butterfly III": "🦋",
        "Explorer": "🗺️",
        "Meme Maker": "😂",
        "Knowledge Keeper": "📚",
        "Holiday Greeter": "🎄",
        "Night Owl": "🦉",
        "Early Bird": "🐦",
        "Daily Devotee": "🗓️",
        "Weekend Warrior": "⚔️",
        "First Anniversary": "🎂",
        "Veteran": "🎖️",
        "Boost Buddy": "✨",
        "The Collector": "🎨",
        "Reaction Responder": "💬",
        "First Steps": "👣",
        "Bot Buddy": "🤖",
        "Voice Veteran": "🗣️",
        "Loyal Listener": "🎧",
        "The Echo": "🤫",
        "Midnight Mystery": "🕛",
        "Zero Gravity": "🪐",
        "Time Capsule": "⏳",
        "Palindrome Pro": "🔄",
        "The Unmentionable": "🤐",
        "I'm Not Listening": "🙉",
        "Code Breaker": "❄️",
        "Ghost Hunter": "👻",
        "Invisible Ink": "✒️",
        "Echo Chamber": "📢",
        "Shadow Lurker": "🚶",
        "Phantom Poster": "✍️",
        "Secret Admirer": "❤️",
        "Error 404": "🔍",
        "Ping Master": "🔔"
    }

    def __init__(self, bot):
        self.bot = bot
        self.data = defaultdict(lambda: {
            "general_unlocked": [],
            "hidden_unlocked": [],
            "message_count": 0,
            "reaction_count": 0,
            "different_reactions": set(),
            "last_message_date": None,
            "daily_streak": 0,
            "weekend_streak": 0,
            "command_count": 0,
            "voice_time": 0.0,
            "first_command_used": False,
            "last_message_text": None,
            "edited_messages_count": 0,
            "join_date": None,
            "last_dm_text": None,
            "channels_visited": set(),
            "message_ids_reacted_to": set(),
            "reaction_responder_count": 0,
            "last_edit_time": None,
            "bot_interactions": 0,
            "helper_hero_count": 0,
            "link_count": 0,
            "consecutive_messages": 0,
            "last_lurker_message": None,
            "meme_count": 0,
            "last_weekend_date": None,
            "edit_timestamps": [],
            "holidays_sent": set(),
            "has_boosted": False,
            "bot_pinged": False,
        })
        self.load_data()
        self.voice_update_task.start()
        self.daily_achievements_update.start()
        self.current_message = None

    def load_data(self):
        if os.path.exists(ACHIEVEMENT_DATA_PATH):
            with open(ACHIEVEMENT_DATA_PATH, 'r') as f:
                data = json.load(f)
                for user_id, user_data in data.items():
                    user_id = int(user_id)
                    user_data["different_reactions"] = set(user_data["different_reactions"])
                    user_data["channels_visited"] = set(user_data["channels_visited"])
                    user_data["message_ids_reacted_to"] = set(user_data["message_ids_reacted_to"])
                    user_data["holidays_sent"] = set(user_data["holidays_sent"])

                    # Convert ISO strings back to datetime objects
                    user_data["last_message_date"] = (
                        datetime.datetime.fromisoformat(user_data["last_message_date"])
                        if user_data["last_message_date"]
                        else None
                    )
                    user_data["last_edit_time"] = (
                        datetime.datetime.fromisoformat(user_data.get("last_edit_time"))
                        if user_data.get("last_edit_time")
                        else None
                    )
                    user_data["last_lurker_message"] = (
                        datetime.datetime.fromisoformat(user_data.get("last_lurker_message"))
                        if user_data.get("last_lurker_message")
                        else None
                    )
                    user_data["last_weekend_date"] = (
                        datetime.date.fromisoformat(user_data.get("last_weekend_date"))
                        if user_data.get("last_weekend_date")
                        else None
                    )
                    user_data["edit_timestamps"] = [
                        datetime.datetime.fromisoformat(ts)
                        for ts in user_data.get("edit_timestamps", [])
                    ]
                    user_data["voice_join_time"] = (
                        datetime.datetime.fromisoformat(user_data.get("voice_join_time"))
                        if user_data.get("voice_join_time")
                        else None
                    )
                    self.data[user_id] = user_data
        else:
            if not os.path.exists('data'):
                os.makedirs('data')
            self.save_data()
    def save_data(self):
        with open(ACHIEVEMENT_DATA_PATH, 'w') as f:
            serializable_data = {}
            for user_id, user_data in self.data.items():
                serializable_data[user_id] = {
                    **user_data,
                    "different_reactions": list(user_data["different_reactions"]),
                    "channels_visited": list(user_data["channels_visited"]),
                    "message_ids_reacted_to": list(user_data["message_ids_reacted_to"]),
                    "holidays_sent": list(user_data["holidays_sent"]),
                    "last_message_date": (
                        user_data["last_message_date"].isoformat()
                        if user_data["last_message_date"]
                        else None
                    ),
                    "last_edit_time": (
                        user_data["last_edit_time"].isoformat()
                        if user_data.get("last_edit_time")
                        else None
                    ),
                    "last_lurker_message": (
                        user_data["last_lurker_message"].isoformat()
                        if user_data.get("last_lurker_message")
                        else None
                    ),
                    "last_weekend_date": (
                        user_data["last_weekend_date"].isoformat()
                        if user_data.get("last_weekend_date")
                        else None
                    ),
                    "edit_timestamps": [
                        ts.isoformat() for ts in user_data.get("edit_timestamps", [])
                    ],
                    "voice_join_time": (
                        user_data.get("voice_join_time").isoformat()
                        if user_data.get("voice_join_time")
                        else None
                    ),
                }
            json.dump(serializable_data, f, indent=4)
    def cog_unload(self):
        self.voice_update_task.cancel()
        self.daily_achievements_update.cancel()

    async def _send_achievement_notification(self, member, achievement_name, is_hidden):
        try:
            channel = self.bot.get_channel(ACHIEVEMENT_ALERT_CHANNEL_ID)
            if not channel:
                print(f"Error: Achievement alert channel with ID {ACHIEVEMENT_ALERT_CHANNEL_ID} not found.")
                return

            emoji = self.ACHIEVEMENT_EMOJI_MAP.get(achievement_name, '🏆' if not is_hidden else '🤫')
            title = f"{emoji} 새로운 업적 달성! {emoji}"
            description = (
                f"{member.mention} 님이 **{achievement_name}** 업적을 달성했습니다!\n"
                f"🎉 축하합니다!"
            )

            embed = discord.Embed(
                title=title,
                description=description,
                color=discord.Color.gold(),
                timestamp=datetime.datetime.now(datetime.timezone.utc)
            )

            if member.avatar:
                embed.set_thumbnail(url=member.avatar.url)

            await channel.send(embed=embed)
            print(f"Achievement embed for {member.name} ({achievement_name}) sent to channel {channel.name}.")

        except Exception as e:
            print(f"Failed to send achievement notification for {member.id} ({achievement_name}): {e}")

    def unlock_achievement(self, user, achievement_name, is_hidden=False):
        user_id = user.id
        user_data = self.data[user_id]
        unlocked_list = user_data["hidden_unlocked"] if is_hidden else user_data["general_unlocked"]
        if achievement_name not in unlocked_list:
            unlocked_list.append(achievement_name)
            self.save_data()
            print(f"Achievement Unlocked for {user_id}: {achievement_name}")
            self.bot.loop.create_task(self._send_achievement_notification(user, achievement_name, is_hidden))
            self.bot.loop.create_task(self.post_achievements_display())

            if not is_hidden and len(user_data["general_unlocked"]) >= 10:
                self.unlock_achievement(user, "Achievement Hunter")
            return True
        return False

    async def _get_sorted_members(self):
        guild = self.bot.get_guild(GUILD_ID)
        if not guild:
            return []

        member_achievements = []
        for member in guild.members:
            if not member.bot:
                user_data = self.data.get(member.id, {"general_unlocked": [], "hidden_unlocked": []})
                unlocked_count = len(user_data["general_unlocked"]) + len(user_data["hidden_unlocked"])
                if unlocked_count > 0:
                    member_achievements.append({'member': member, 'count': unlocked_count})

        sorted_members = sorted(member_achievements, key=lambda x: x['count'], reverse=True)
        return [item['member'] for item in sorted_members]

    async def post_achievements_display(self):
        channel = self.bot.get_channel(ACHIEVEMENT_CHANNEL_ID)
        if not channel:
            print(f"Error: Leaderboard channel with ID {ACHIEVEMENT_CHANNEL_ID} not found.")
            return

        try:
            async for message in channel.history(limit=50):
                if message.author == self.bot.user and message.embeds and (
                        "업적 현황" in message.embeds[0].title or "업적 목록 및 힌트" in message.embeds[0].title):
                    try:
                        await message.delete()
                        print(f"이전 업적 메시지 삭제 완료 (ID: {message.id})")
                    except discord.NotFound:
                        pass

            list_embed = await self._create_achievement_list_embed()
            await channel.send(embed=list_embed)
            print("업적 목록 및 힌트 메시지 게시 완료.")

            sorted_members = await self._get_sorted_members()
            if sorted_members:
                view = PersistentAchievementView(self.bot)
                # Pass cog and members to get_current_embed
                cog = self.bot.get_cog("Achievements")
                initial_embed = await view.get_current_embed(cog, sorted_members)
                self.current_message = await channel.send(embed=initial_embed, view=view)
                print(f"업적 현황 메시지 게시 완료.")
            else:
                await channel.send("업적을 달성한 멤버가 없습니다.")

        except Exception as e:
            print(f"업적 현황 메시지 게시 실패: {e}\n{traceback.format_exc()}")
    async def _create_achievements_embed(self, member: discord.Member, rank: int, total_members: int) -> discord.Embed:
        user_id = member.id
        user_data = self.data.get(user_id, defaultdict(lambda: {"general_unlocked": [], "hidden_unlocked": []}))
        general_unlocked = user_data["general_unlocked"]
        hidden_unlocked = user_data["hidden_unlocked"]

        total_general = len(self.GENERAL_ACHIEVEMENTS)
        total_hidden = len(self.HIDDEN_ACHIEVEMENTS)
        total_achievements = total_general + total_hidden
        unlocked_count = len(general_unlocked) + len(hidden_unlocked)
        progress = f"{unlocked_count}/{total_achievements}"

        embed = discord.Embed(
            title=f"업적 현황 - {member.display_name} (Rank {rank}/{total_members})",
            description=f"업적 달성 현황: {progress}",
            color=discord.Color.blue()
        )
        embed.set_thumbnail(url=member.avatar.url if member.avatar else member.default_avatar.url)

        if general_unlocked:
            general_list = ""
            for ach in general_unlocked:
                emoji = self.ACHIEVEMENT_EMOJI_MAP.get(ach, '🏆')
                general_list += f"{emoji} {ach}\n"
            embed.add_field(name=f"🏆 일반 업적 ({len(general_unlocked)}/{total_general})",
                            value=general_list.strip() or "아직 달성한 일반 업적이 없습니다.", inline=False)
        else:
            embed.add_field(name=f"🏆 일반 업적 (0/{total_general})", value="아직 달성한 일반 업적이 없습니다.", inline=False)

        if hidden_unlocked:
            hidden_list = ""
            for ach in hidden_unlocked:
                emoji = self.ACHIEVEMENT_EMOJI_MAP.get(ach, '🤫')
                hidden_list += f"{emoji} {ach}\n"
            embed.add_field(name=f"🤫 히든 업적 ({len(hidden_unlocked)}/{total_hidden})",
                            value=hidden_list.strip() or "아직 달성한 히든 업적이 없습니다.", inline=False)
        else:
            embed.add_field(name=f"🤫 히든 업적 (0/{total_hidden})", value="아직 달성한 히든 업적이 없습니다.", inline=False)

        return embed
    async def _create_achievement_list_embed(self) -> discord.Embed:
        general_list = "\n".join(f"**{name}**: {desc}" for name, desc in self.GENERAL_ACHIEVEMENTS.items())
        hidden_list = "\n".join(f"**{name}**: {desc}" for name, desc in self.HIDDEN_ACHIEVEMENTS.items())

        embed = discord.Embed(
            title="업적 목록 및 힌트",
            description="아래는 봇에서 달성할 수 있는 모든 업적 목록입니다.",
            color=discord.Color.green()
        )
        embed.add_field(name=f"일반 업적 ({len(self.GENERAL_ACHIEVEMENTS)})", value=general_list, inline=False)
        embed.add_field(name=f"히든 업적 ({len(self.HIDDEN_ACHIEVEMENTS)})", value=hidden_list, inline=False)
        return embed

    @commands.Cog.listener()
    async def on_ready(self):
        print("Achievements cog loaded.")
        if ACHIEVEMENT_CHANNEL_ID:
            print("Bot starting up. Posting achievements display.")
            await self.post_achievements_display()
            print("Initial achievements display posted.")

    @tasks.loop(time=datetime.time(0, 0, 0, tzinfo=LOCAL_SERVER_TZ))
    async def daily_achievements_update(self):
        try:
            print("Daily achievement update starting.")
            await self.post_achievements_display()
            print("Daily achievement update completed.")
        except Exception as e:
            print(f"Daily achievement update failed: {e}\n{traceback.format_exc()}")

    @commands.Cog.listener()
    async def on_member_join(self, member):
        if member.bot: return
        self.data[member.id]["join_date"] = member.joined_at.isoformat()
        self.save_data()

    @commands.Cog.listener()
    async def on_member_update(self, before, after):
        if before.premium_since is None and after.premium_since is not None:
            user_data = self.data[after.id]
            if not user_data.get("has_boosted"):
                self.unlock_achievement(after, "Boost Buddy")
                user_data["has_boosted"] = True
                self.save_data()

    @commands.Cog.listener()
    async def on_message(self, message):
        if message.author.bot:
            return

        user_id = message.author.id
        user_data = self.data[user_id]
        now = datetime.datetime.now(datetime.timezone.utc)

        # NEW CODE TO HANDLE "Error 404" ACHIEVEMENT FOR SLASH COMMANDS
        # The code will check if a message starts with a slash and is not a valid registered command.
        if message.content.startswith('/') and message.guild:
            try:
                # The first word after the slash is the command name.
                command_name = message.content.split(' ')[0][1:].lower()

                # We need to get a list of all registered slash commands.
                # Since the command tree is per guild, we check the commands for the message's guild.
                all_slash_commands = [c.name.lower() for c in self.bot.tree.get_commands(guild=message.guild)]

                # Check if the command exists. If it doesn't, unlock the achievement.
                if command_name not in all_slash_commands:
                    self.unlock_achievement(message.author, "Error 404", is_hidden=True)
            except IndexError:
                # This handles cases where the message is just "/" with nothing else.
                pass

        if isinstance(message.channel, discord.DMChannel):
            if "안녕" in message.content:
                self.unlock_achievement(message.author, "The Echo", is_hidden=True)
            self.save_data()
            return

        if not user_data.get("join_date"):
            user_data["join_date"] = message.author.joined_at.isoformat()

        join_date = datetime.datetime.fromisoformat(user_data["join_date"])
        if now.month == join_date.month and now.day == join_date.day:
            self.unlock_achievement(message.author, "First Anniversary")

        if (now - join_date).days >= 365:
            self.unlock_achievement(message.author, "Veteran")

        user_data["message_count"] += 1
        user_data["channels_visited"].add(message.channel.id)
        if len(user_data["channels_visited"]) >= 10:
            self.unlock_achievement(message.author, "Explorer")

        if user_data["message_count"] >= 100: self.unlock_achievement(message.author, "Social Butterfly I")
        if user_data["message_count"] >= 500: self.unlock_achievement(message.author, "Social Butterfly II")
        if user_data["message_count"] >= 1000: self.unlock_achievement(message.author, "Social Butterfly III")

        if message.attachments or message.embeds:
            user_data["meme_count"] = user_data.get("meme_count", 0) + 1
            if user_data["meme_count"] >= 50: self.unlock_achievement(message.author, "Meme Maker")

        if "http" in message.content or "www" in message.content:
            user_data["link_count"] = user_data.get("link_count", 0) + 1
            if user_data["link_count"] >= 20: self.unlock_achievement(message.author, "Knowledge Keeper")

        today = now.strftime("%B %d").lower()
        if today in HOLIDAYS:
            if today not in user_data["holidays_sent"]:
                user_data["holidays_sent"].add(today)
                if len(user_data["holidays_sent"]) >= 5:
                    self.unlock_achievement(message.author, "Holiday Greeter")
                self.unlock_achievement(message.author, "The Time Traveler", is_hidden=True)

        now_local = now.astimezone(datetime.timezone(timedelta(hours=message.guild.id % 24)))
        if 5 <= now_local.hour < 6: self.unlock_achievement(message.author, "Night Owl")
        if 9 <= now_local.hour < 10: self.unlock_achievement(message.author, "Early Bird")
        if now_local.hour == 0 and now_local.minute == 0: self.unlock_achievement(message.author, "Midnight Mystery",
                                                                                  is_hidden=True)

        last_message_date = user_data["last_message_date"]
        if last_message_date and now.date() == last_message_date.date() + timedelta(days=1):
            user_data["daily_streak"] = user_data.get("daily_streak", 0) + 1
        elif not last_message_date or now.date() != last_message_date.date():
            user_data["daily_streak"] = 1
        user_data["last_message_date"] = now
        if user_data["daily_streak"] >= 7: self.unlock_achievement(message.author, "Daily Devotee")

        if now.weekday() >= 5:
            if not user_data.get("last_weekend_date") or (now.date() - user_data["last_weekend_date"]).days > 2:
                user_data["weekend_streak"] = 1
            else:
                user_data["weekend_streak"] = user_data.get("weekend_streak", 0) + 1
            user_data["last_weekend_date"] = now.date()
            if user_data["weekend_streak"] >= 10: self.unlock_achievement(message.author, "Weekend Warrior")

        online_members = [m for m in message.guild.members if m.status != discord.Status.offline and not m.bot]
        if len(online_members) == 1 and online_members[0].id == message.author.id:
            self.unlock_achievement(message.author, "Zero Gravity", is_hidden=True)

        if message.reference:
            try:
                referenced_message = await message.channel.fetch_message(message.reference.message_id)
                if (now - referenced_message.created_at).days >= 365:
                    self.unlock_achievement(message.author, "Time Capsule", is_hidden=True)
            except discord.NotFound:
                pass

        cleaned_content = ''.join(char.lower() for char in message.content if char.isalnum())
        if cleaned_content and cleaned_content == cleaned_content[::-1] and len(cleaned_content) > 2:
            self.unlock_achievement(message.author, "Palindrome Pro", is_hidden=True)
        if "사랑해" in message.content:
            self.unlock_achievement(message.author, "The Unmentionable", is_hidden=True)
        if "멸망전" in message.content:
            self.unlock_achievement(message.author, "I'm Not Listening", is_hidden=True)
        if '❄️' in message.content:
            self.unlock_achievement(message.author, "Code Breaker", is_hidden=True)
        if message.mentions and message.mentions[0].id == GHOST_HUNTER_ID:
            self.unlock_achievement(message.author, "Ghost Hunter", is_hidden=True)
        if '||' in message.content:
            self.unlock_achievement(message.author, "Invisible Ink", is_hidden=True)

        if user_data.get("last_message_text") == message.content:
            user_data["consecutive_messages"] = user_data.get("consecutive_messages", 0) + 1
            if user_data["consecutive_messages"] >= 3:
                self.unlock_achievement(message.author, "Echo Chamber", is_hidden=True)
        else:
            user_data["consecutive_messages"] = 1
        user_data["last_message_text"] = message.content

        if user_data.get("last_lurker_message") and (now - user_data["last_lurker_message"]).days >= 7:
            self.unlock_achievement(message.author, "Shadow Lurker", is_hidden=True)
        user_data["last_lurker_message"] = now

        self.save_data()

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload):
        if payload.member and payload.member.bot:
            return

        user_id = payload.user_id
        user_data = self.data[user_id]
        emoji_id = str(payload.emoji)
        user_data["reaction_count"] += 1
        user_data["different_reactions"].add(emoji_id)
        user_data["message_ids_reacted_to"].add(payload.message_id)

        if len(user_data["different_reactions"]) >= 10:
            self.unlock_achievement(self.bot.get_user(user_id), "The Collector")

        if len(user_data["message_ids_reacted_to"]) >= 50:
            self.unlock_achievement(self.bot.get_user(user_id), "Reaction Responder")

        if payload.emoji.name == '❤️':
            channel = self.bot.get_channel(payload.channel_id)
            message = await channel.fetch_message(payload.message_id)
            if message.author.id == self.bot.user.id:
                self.unlock_achievement(self.bot.get_user(user_id), "Secret Admirer", is_hidden=True)

        self.save_data()

    @commands.Cog.listener()
    async def on_command_error(self, ctx, error):
        if isinstance(error, commands.CommandNotFound):
            self.unlock_achievement(ctx.author, "Error 404", is_hidden=True)

    @commands.Cog.listener()
    async def on_message_edit(self, before, after):
        if after.author.bot: return
        user_id = after.author.id
        user_data = self.data[user_id]

        now = datetime.datetime.now(datetime.timezone.utc)
        user_data["edit_timestamps"] = [ts for ts in user_data["edit_timestamps"] if (now - ts).total_seconds() <= 60]
        user_data["edit_timestamps"].append(now)

        if len(user_data["edit_timestamps"]) >= 5:
            self.unlock_achievement(after.author, "Phantom Poster", is_hidden=True)

        self.save_data()

    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):
        if member.bot: return
        if before.channel is None and after.channel is not None:
            self.data[member.id]["voice_join_time"] = datetime.datetime.now()
        elif before.channel is not None and after.channel is None and "voice_join_time" in self.data[member.id]:
            duration = datetime.datetime.now() - self.data[member.id]["voice_join_time"]
            self.data[member.id]["voice_time"] += duration.total_seconds()
            del self.data[member.id]["voice_join_time"]
            self.save_data()

    @tasks.loop(minutes=1)
    async def voice_update_task(self):
        guild = self.bot.get_guild(GUILD_ID)
        if not guild:
            return
        now = datetime.datetime.now()
        for member_id, user_data in self.data.items():
            member = guild.get_member(member_id)
            if member and member.voice and member.voice.channel and "voice_join_time" in user_data:
                duration = now - user_data["voice_join_time"]
                user_data["voice_time"] += duration.total_seconds()
                user_data["voice_join_time"] = now
                if user_data["voice_time"] >= 36000:
                    self.unlock_achievement(member, "Voice Veteran")
                if user_data["voice_time"] >= 180000:
                    self.unlock_achievement(member, "Loyal Listener")
        self.save_data()

    @app_commands.command(name="achievements", description="Shows a member's achievements.")
    async def achievements_command(self, interaction: discord.Interaction, member: Optional[discord.Member] = None):
        sorted_members = await self._get_sorted_members()

        if member:
            try:
                index = next(i for i, m in enumerate(sorted_members) if m.id == member.id)
                view = PersistentAchievementView(sorted_members, self)
                view.current_page = index
                initial_embed = await view.get_current_embed()
                await interaction.response.send_message(embed=initial_embed, view=view, ephemeral=True)
            except StopIteration:
                await interaction.response.send_message(
                    f"Member {member.display_name} not found in the achievement leaderboard.", ephemeral=True)
        else:
            if not sorted_members:
                await interaction.response.send_message("No members found with achievements.", ephemeral=True)
                return

            view = PersistentAchievementView(self.bot)
            initial_embed = await view.get_current_embed()
            await interaction.response.send_message(embed=initial_embed, view=view)


async def setup(bot):
    await bot.add_cog(Achievements(bot))
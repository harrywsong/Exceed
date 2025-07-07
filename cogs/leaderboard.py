import discord
from discord import app_commands
from discord.ext import commands, tasks
from typing import Optional, List

from pip._internal.cli.cmdoptions import progress_bar

from utils import config
from utils.logger import get_logger
from datetime import datetime, time, timedelta
import pytz
import asyncio
import urllib.parse

from typing import Optional

LEADERBOARD_PAGE_SIZE = 5
EASTERN_TZ = pytz.timezone("US/Eastern")


class LeaderboardView(discord.ui.View):
    def __init__(self, cog, interaction: Optional[discord.Interaction], entries: List[dict]):
        super().__init__(timeout=120)
        self.cog = cog
        self.interaction = interaction  # can be None
        self.entries = entries
        self.page = 0
        self.message: discord.Message = None

        self.prev_button.disabled = True
        if len(entries) <= LEADERBOARD_PAGE_SIZE:
            self.next_button.disabled = True

    def get_max_page(self):
        return max(0, (len(self.entries) - 1) // LEADERBOARD_PAGE_SIZE)

    def progress_bar(self, page, max_page, length=10):
        filled = int((page + 1) / (max_page + 1) * length)
        bar = "🟩" * filled + "⬜" * (length - filled)
        return bar

    def build_embed(self):
        start_idx = self.page * LEADERBOARD_PAGE_SIZE
        end_idx = start_idx + LEADERBOARD_PAGE_SIZE
        page_entries = self.entries[start_idx:end_idx]

        embed = discord.Embed(
            title="🏆 Exceed 클랜 리더보드",
            description=f"\n👥 총 멤버: {len(self.entries)}명 \n📅 리더보드는 매일 새벽 12시에 (미 동부 시간) 갱신됩니다.\n\n",
            color=discord.Color.gold()
        )

        medal_emojis = {1: "🥇", 2: "🥈", 3: "🥉"}

        for i, entry in enumerate(page_entries, start=start_idx + 1):
            medal = medal_emojis.get(i, f"`#{i}`")

            user_mention = f"<@{entry['discord_id']}>" if entry.get("discord_id") else "❔"
            riot_name = entry.get("name", "알 수 없음")

            # URL encode Riot ID for safe URL use
            encoded_name = urllib.parse.quote(riot_name)
            tracker_url = f"https://tracker.gg/valorant/profile/riot/{encoded_name}/overview"
            riot_id_link = f"[{riot_name}]({tracker_url})"

            score = entry.get("total_points") or 0
            kills = entry.get("kills") or 0
            deaths = entry.get("deaths") or 0
            kd_ratio = entry.get("kd_ratio") or 0
            matches_played = entry.get("matches_played") or 0

            embed.add_field(
                name="\u200b",
                value=(
                    f"{medal}\n"
                    f"{user_mention}\n"
                    f"Riot ID: {riot_id_link}\n"
                    f"📊 점수: `{score:.1f}`  ⚔️ K/D: `{kd_ratio:.2f}`\n"
                    f"🟥 Kills: `{kills}`  🟦 Deaths: `{deaths}`\n"
                    f"🧮 매치 수: `{matches_played}`"
                ),
                inline=False,
            )

        embed.set_footer(
            text=(
                "\u2003\u2003"  # two em spaces as padding
                f"페이지 {self.page + 1} / {self.get_max_page() + 1}  |  "
                f"{self.progress_bar(self.page, self.get_max_page())}"
            )
        )

        return embed

    async def update_message(self):
        embed = self.build_embed()
        self.prev_button.disabled = self.page == 0
        self.next_button.disabled = self.page == self.get_max_page()
        await self.message.edit(embed=embed, view=self)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        # If self.interaction is None (like on startup), allow all users to use buttons
        if self.interaction is None:
            return True

        if interaction.user.id != self.interaction.user.id:
            await interaction.response.send_message(
                "이 버튼을 사용할 권한이 없습니다.", ephemeral=True
            )
            return False
        return True

    @discord.ui.button(label="이전", style=discord.ButtonStyle.secondary)
    async def prev_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        if self.page > 0:
            self.page -= 1
            await self.update_message()
        await interaction.response.defer()

    @discord.ui.button(label="다음", style=discord.ButtonStyle.secondary)
    async def next_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        if self.page < self.get_max_page():
            self.page += 1
            await self.update_message()
        await interaction.response.defer()


class ClanLeaderboard(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.leaderboard_messages = {}  # message_id -> view

        self.logger = get_logger(
            "clanleaderboard",
            bot=bot,
            discord_log_channel_id=config.LOG_CHANNEL_ID,
        )

        self.leaderboard_channel = None
        self.current_message = None

        # Start background task after bot is ready
        self.bot.loop.create_task(self.wait_until_ready())

    async def wait_until_ready(self):
        await self.bot.wait_until_ready()
        self.leaderboard_channel = self.bot.get_channel(config.CLAN_LEADERBOARD_CHANNEL_ID)
        if not self.leaderboard_channel:
            self.logger.error(
                f"CLAN_LEADERBOARD_CHANNEL_ID {config.CLAN_LEADERBOARD_CHANNEL_ID} 채널을 찾을 수 없습니다."
            )
            return

        # On startup, post leaderboard
        await self.post_leaderboard()
        self.logger.info("봇 시작 시 리더보드 게시 완료.")

        # Start the daily update task
        self.daily_leaderboard_update.start()

    async def fetch_leaderboard_data(self) -> List[dict]:
        async with self.bot.pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT discord_id,
                       name,
                       COUNT(*)         AS matches_played,
                       SUM(total_points) AS total_points,
                       SUM(kills)        AS kills,
                       SUM(deaths)       AS deaths,
                       CASE
                           WHEN SUM(deaths) = 0 THEN SUM(kills)::float
                           ELSE SUM(kills)::float / SUM(deaths)
                       END AS kd_ratio
                FROM clan
                WHERE discord_id IS NOT NULL
                GROUP BY discord_id, name
                ORDER BY total_points DESC
                LIMIT 50
                """
            )
            return [dict(row) for row in rows]

    async def post_leaderboard(self, interaction: Optional[discord.Interaction] = None):
        if not self.leaderboard_channel:
            self.logger.error("리더보드 채널이 설정되지 않았습니다.")
            return

        entries = await self.fetch_leaderboard_data()
        if not entries:
            self.logger.warning("리더보드에 표시할 데이터가 없습니다.")
            if interaction:
                await interaction.followup.send("❌ 리더보드에 표시할 데이터가 없습니다.", ephemeral=True)
            return

        # Delete old leaderboard messages posted by bot in the channel (optional: limit to last 50 messages)
        try:
            async for msg in self.leaderboard_channel.history(limit=50):
                if msg.author == self.bot.user and msg.embeds:
                    # Check embed title to identify leaderboard messages (basic check)
                    if any("클랜 리더보드" in embed.title for embed in msg.embeds):
                        await msg.delete()
                        self.logger.info(f"기존 리더보드 메시지 삭제됨 (ID: {msg.id})")
        except Exception as e:
            self.logger.error(f"리더보드 메시지 삭제 실패: {e}")

        view = LeaderboardView(self, interaction, entries)
        msg = await self.leaderboard_channel.send(embed=view.build_embed(), view=view)
        view.message = msg
        self.current_message = msg

        if interaction:
            await interaction.followup.send(f"✅ 클랜 리더보드가 {self.leaderboard_channel.mention} 채널에 게시되었습니다.", ephemeral=True)
        self.logger.info(f"클랜 리더보드 게시 완료 (메시지 ID: {msg.id})")

    @app_commands.command(name="leaderboard", description="클랜 멤버 상위 플레이어들을 점수 순으로 보여줍니다.")
    async def leaderboard(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        await self.post_leaderboard(interaction)

    @tasks.loop(hours=24)
    async def daily_leaderboard_update(self):
        # Calculate next 4 AM Eastern time from now
        now = datetime.now(tz=EASTERN_TZ)
        target_time = time(4, 0, 0)
        next_4am = datetime.combine(now.date(), target_time).replace(tzinfo=EASTERN_TZ)
        if now >= next_4am:
            next_4am += timedelta(days=1)
        wait_seconds = (next_4am - now).total_seconds()
        self.logger.info(f"다음 리더보드 업데이트까지 대기: {wait_seconds:.1f}초")
        await asyncio.sleep(wait_seconds)

        while True:
            try:
                await self.post_leaderboard()
                self.logger.info("일일 리더보드 업데이트 완료.")
            except Exception as e:
                self.logger.error(f"일일 리더보드 업데이트 실패: {e}")

            # Wait exactly 24 hours until next run
            await asyncio.sleep(24 * 3600)

    @daily_leaderboard_update.before_loop
    async def before_daily_leaderboard_update(self):
        await self.bot.wait_until_ready()

async def setup(bot: commands.Bot):
    await bot.add_cog(ClanLeaderboard(bot))

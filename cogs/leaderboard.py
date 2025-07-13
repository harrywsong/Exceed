import discord
from discord import app_commands
from discord.ext import commands, tasks
from typing import Optional, List
import traceback

from utils import config
from utils.logger import get_logger
from datetime import time
import pytz
import asyncio
import urllib.parse

LEADERBOARD_PAGE_SIZE = 5
EASTERN_TZ = pytz.timezone("US/Eastern")


class LeaderboardView(discord.ui.View):
    def __init__(self, cog, interaction: Optional[discord.Interaction], entries: List[dict]):
        super().__init__(timeout=120)
        self.cog = cog
        self.interaction = interaction
        self.entries = entries
        self.page = 0
        self.message: discord.Message = None

        self.prev_button.disabled = True
        if len(entries) <= LEADERBOARD_PAGE_SIZE:
            self.next_button.disabled = True

    def get_max_page(self):
        return max(0, (len(self.entries) - 1) // LEADERBOARD_PAGE_SIZE)

    def progress_bar(self, page, max_page, length=10):
        if max_page == 0:
            return "🟩" * length
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

            encoded_name = urllib.parse.quote(riot_name)
            tracker_url = f"https://tracker.gg/valorant/profile/riot/{encoded_name}/overview"
            riot_id_link = f"[{riot_name}]({tracker_url})"

            score = entry.get("total_points") or 0
            kills = entry.get("kills") or 0
            deaths = entry.get("deaths") or 0
            kd_ratio = entry.get("kd_ratio") if entry.get("deaths", 0) != 0 else entry.get("kills", 0)
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
                "\u2003\u2003"
                f"페이지 {self.page + 1} / {self.get_max_page() + 1}  |  "
                f"{self.progress_bar(self.page, self.get_max_page())}"
            )
        )

        return embed

    async def update_message(self):
        if not self.message:
            self.cog.logger.error("LeaderboardView: message attribute is not set for update. Cannot edit message.")
            self.stop()
            return

        embed = self.build_embed()
        self.prev_button.disabled = self.page == 0
        self.next_button.disabled = self.page == self.get_max_page()
        try:
            await self.message.edit(embed=embed, view=self)
        except discord.HTTPException as e:
            self.cog.logger.error(
                f"Failed to edit leaderboard message {self.message.id}: {e}\n{traceback.format_exc()}")
            self.stop()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
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
        self.leaderboard_messages = {}

        self.logger = get_logger(
            "클랜 리더보드",
            bot=bot,
            discord_log_channel_id=config.LOG_CHANNEL_ID,
        )
        self.logger.info("ClanLeaderboard cog initialized.")

        self.leaderboard_channel = None
        self.current_message = None

    async def fetch_leaderboard_data(self) -> List[dict]:
        try:
            async with self.bot.pool.acquire() as conn:
                rows = await conn.fetch(
                    """
                    SELECT discord_id,
                           name,
                           COUNT(*)          AS matches_played,
                           SUM(total_points) AS total_points,
                           SUM(kills)        AS kills,
                           SUM(deaths)       AS deaths,
                           CASE
                               WHEN SUM(deaths) = 0 THEN SUM(kills)::float
                               ELSE SUM(kills)::float / SUM(deaths)
                    END
                    AS kd_ratio
                    FROM clan
                    WHERE discord_id IS NOT NULL
                    GROUP BY discord_id, name
                    ORDER BY total_points DESC
                    LIMIT 50
                    """
                )
                return [dict(row) for row in rows]
        except Exception as e:
            self.logger.error(f"리더보드 데이터베이스 쿼리 실패: {e}\n{traceback.format_exc()}")
            return []

    async def post_leaderboard(self, interaction: Optional[discord.Interaction] = None):
        if not self.leaderboard_channel:
            self.leaderboard_channel = self.bot.get_channel(config.CLAN_LEADERBOARD_CHANNEL_ID)
            if not self.leaderboard_channel:
                self.logger.error(
                    f"CLAN_LEADERBOARD_CHANNEL_ID {config.CLAN_LEADERBOARD_CHANNEL_ID} 채널을 찾을 수 없습니다. 메시지를 게시할 수 없습니다."
                )
                if interaction:
                    await interaction.followup.send("❌ 리더보드 채널이 설정되지 않았습니다. 관리자에게 문의해주세요.", ephemeral=True)
                return

        entries = await self.fetch_leaderboard_data()
        if not entries:
            self.logger.warning("리더보드에 표시할 데이터가 없습니다.")
            if interaction:
                await interaction.followup.send("❌ 리더보드에 표시할 데이터가 없습니다.", ephemeral=True)
            return

        try:
            self.logger.info(f"기존 리더보드 메시지 정리 시작 (채널: #{self.leaderboard_channel.name}).")
            deleted_count = 0
            async for msg in self.leaderboard_channel.history(limit=5):
                if msg.author == self.bot.user and msg.embeds:
                    if any("클랜 리더보드" in embed.title for embed in msg.embeds):
                        await msg.delete()
                        deleted_count += 1
                        self.logger.info(f"기존 리더보드 메시지 삭제됨 (ID: {msg.id})")
                        await asyncio.sleep(1)

            if deleted_count > 0:
                self.logger.info(f"총 {deleted_count}개의 기존 리더보드 메시지 삭제 완료.")
                await asyncio.sleep(2)
            else:
                self.logger.info("삭제할 기존 리더보드 메시지가 없습니다.")

        except discord.Forbidden:
            self.logger.error(f"리더보드 채널에서 메시지를 삭제할 권한이 없습니다. 봇 권한을 확인해주세요. {traceback.format_exc()}")
            if interaction:
                await interaction.followup.send("❌ 봇이 기존 리더보드 메시지를 삭제할 권한이 없습니다.", ephemeral=True)
        except discord.HTTPException as e:
            self.logger.error(f"리더보드 메시지 삭제 중 HTTP 오류 발생: {e}\n{traceback.format_exc()}")
            if interaction:
                await interaction.followup.send(f"❌ 기존 리더보드 메시지 삭제 중 오류 발생: `{e}`", ephemeral=True)
        except Exception as e:
            self.logger.error(f"리더보드 메시지 삭제 중 알 수 없는 오류 발생: {e}\n{traceback.format_exc()}")
            if interaction:
                await interaction.followup.send(f"❌ 기존 리더보드 메시지 삭제 중 알 수 없는 오류가 발생했습니다.", ephemeral=True)

        try:
            view = LeaderboardView(self, interaction, entries)
            msg = await self.leaderboard_channel.send(embed=view.build_embed(), view=view)
            view.message = msg
            self.current_message = msg

            if interaction:
                await interaction.followup.send(
                    f"✅ 클랜 리더보드가 {self.leaderboard_channel.mention} 채널에 게시되었습니다.",
                    ephemeral=True,
                )
            self.logger.info(f"클랜 리더보드 게시 완료 (메시지 ID: {msg.id})")
        except Exception as e:
            self.logger.error(f"새 리더보드 메시지 게시 실패: {e}\n{traceback.format_exc()}")
            if interaction:
                await interaction.followup.send(f"❌ 새로운 리더보드 메시지를 게시하는 데 실패했습니다: `{e}`", ephemeral=True)

    @app_commands.command(name="leaderboard", description="클랜 멤버 상위 플레이어들을 점수 순으로 보여줍니다.")
    async def leaderboard(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        await self.post_leaderboard(interaction)

    @tasks.loop(time=time(0, 0, 0, tzinfo=EASTERN_TZ))
    async def daily_leaderboard_update(self):
        try:
            self.logger.info("일일 리더보드 업데이트 시작 (정시 실행).")
            await self.post_leaderboard()
            self.logger.info("일일 리더보드 업데이트 완료 (정시 실행).")
        except Exception as e:
            self.logger.error(f"일일 리더보드 업데이트 실패: {e}\n{traceback.format_exc()}")

    @daily_leaderboard_update.before_loop
    async def before_daily_leaderboard_update(self):
        await self.bot.wait_until_ready()
        self.logger.info("일일 리더보드 업데이트 루프 시작 대기 중...")

async def setup(bot: commands.Bot):
    await bot.add_cog(ClanLeaderboard(bot))
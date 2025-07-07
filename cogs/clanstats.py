import discord
from discord import app_commands
from discord.ext import commands
import asyncpg
from typing import Optional
from utils.logger import get_logger
from utils import config  # your config with LOG_CHANNEL_ID

class ValorantStats(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.logger = get_logger(__name__, bot=bot, discord_log_channel_id=config.LOG_CHANNEL_ID)

    async def save_match_and_clan(self, data: dict, match_uuid: Optional[str] = None):
        async with self.bot.pool.acquire() as conn:
            async with conn.transaction():
                map_name = data.get("map")
                mode = data.get("mode")
                team1_score = data.get("team1_score", 0)
                team2_score = data.get("team2_score", 0)
                round_count = data.get("round_count", 0)

                match_id = await conn.fetchval(
                    """
                    INSERT INTO matches(match_uuid, map, mode, team1_score, team2_score, round_count)
                    VALUES ($1, $2, $3, $4, $5, $6)
                    ON CONFLICT (match_uuid) DO NOTHING
                    RETURNING id
                    """,
                    match_uuid, map_name, mode, team1_score, team2_score, round_count
                )
                if not match_id:
                    match_id = await conn.fetchval("SELECT id FROM matches WHERE match_uuid = $1", match_uuid)
                    self.logger.info(f"기존 매치 UUID로 ID 조회됨: {match_uuid} -> {match_id}")
                else:
                    self.logger.info(f"새로운 매치 저장됨: {match_uuid} -> {match_id}")

                rows = await conn.fetch("SELECT discord_id, riot_id FROM registrations")
                riot_to_discord = {row['riot_id']: row['discord_id'] for row in rows}

                for p in data.get("players", []):
                    riot_id = p.get("name")
                    discord_id = riot_to_discord.get(riot_id)
                    await conn.execute(
                        """
                        INSERT INTO clan(
                            match_id, discord_id, riot_id, name, agent, team, tier,
                            acs, score, kills, deaths, assists, plus_minus,
                            kd_ratio, dda, adr, hs_pct, kast_pct, fk, fd, mk,
                            acs_bonus, round_win_points, total_points
                        ) VALUES (
                            $1, $2, $3, $4, $5, $6, $7,
                            $8, $9, $10, $11, $12, $13,
                            $14, $15, $16, $17, $18, $19, $20, $21,
                            $22, $23, $24
                        )
                        ON CONFLICT DO NOTHING
                        """,
                        match_id, discord_id, riot_id, riot_id, p.get("agent"), p.get("team"), p.get("tier"),
                        p.get("acs"), p.get("score"), p.get("kills"), p.get("deaths"), p.get("assists"), p.get("plus_minus"),
                        p.get("kd_ratio"), p.get("dda"), p.get("adr"), p.get("hs_pct"), p.get("kast_pct"), p.get("fk"),
                        p.get("fd"), p.get("mk"), p.get("acs_bonus"), p.get("round_win_points"), p.get("total_points")
                    )
                    if discord_id is not None:
                        self.logger.info(f"플레이어 저장됨: {riot_id} ({discord_id})")
                    else:
                        self.logger.debug(f"플레이어 저장됨: {riot_id} (Discord ID 없음)")

    @app_commands.command(name="통계", description="최근 매치 요약 통계를 확인합니다.")
    @app_commands.describe(count="최근 포함할 경기 수 (기본값 10, 최대 50)")
    async def mystats(self, interaction: discord.Interaction, count: Optional[int] = 10):
        await interaction.response.defer(ephemeral=True)

        if count is None or count <= 0:
            count = 10
        if count > 50:
            count = 50

        discord_id = interaction.user.id
        self.logger.info(f"{interaction.user.display_name} ({discord_id}) 유저가 /mystats 요청 (최근 {count}경기)")

        async with self.bot.pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT AVG(acs)::float as avg_acs,
                       AVG(kills)::float as avg_kills,
                       AVG(deaths)::float as avg_deaths,
                       AVG(assists)::float as avg_assists,
                       AVG(kd_ratio)::float as avg_kd,
                       AVG(adr)::float as avg_adr,
                       AVG(hs_pct)::float as avg_hs_pct,
                       AVG(total_points)::float as avg_points,
                       COUNT(*) as matches_played
                FROM (SELECT *
                      FROM clan
                      WHERE discord_id = $1
                      ORDER BY id DESC
                      LIMIT $2) AS recent_matches
                """,
                discord_id, count
            )

            if not row or row['matches_played'] == 0:
                await interaction.followup.send("기록된 매치가 없습니다. 먼저 매치를 등록하세요.", ephemeral=True)
                self.logger.warning(f"{discord_id} 유저는 기록된 매치가 없음.")
                return

            embed = discord.Embed(
                title=f"{interaction.user.display_name}님의 발로란트 통계 요약 (최근 {row['matches_played']}경기)",
                color=discord.Color.green()
            )

            embed.add_field(name="평균 ACS", value=f"{row['avg_acs']:.1f}", inline=True)
            embed.add_field(name="평균 킬", value=f"{row['avg_kills']:.1f}", inline=True)
            embed.add_field(name="평균 데스", value=f"{row['avg_deaths']:.1f}", inline=True)
            embed.add_field(name="평균 어시스트", value=f"{row['avg_assists']:.1f}", inline=True)
            embed.add_field(name="K/D 비율", value=f"{row['avg_kd']:.2f}", inline=True)
            embed.add_field(name="평균 ADR", value=f"{row['avg_adr']:.1f}", inline=True)
            embed.add_field(name="평균 헤드샷률", value=f"{row['avg_hs_pct']:.1f}%", inline=True)
            embed.add_field(name="평균 점수", value=f"{row['avg_points']:.1f}", inline=True)

            await interaction.followup.send(embed=embed)
            self.logger.info(f"{interaction.user.display_name}님의 통계 응답 전송 완료.")

async def setup(bot: commands.Bot):
    await bot.add_cog(ValorantStats(bot))

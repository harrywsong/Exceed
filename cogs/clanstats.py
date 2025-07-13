import discord
from discord import app_commands
from discord.ext import commands
import asyncpg
from typing import Optional
import traceback

from utils.logger import get_logger
from utils import config


class ValorantStats(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.logger = get_logger(
            "발로란트 통계",
            bot=self.bot,
            discord_log_channel_id=config.LOG_CHANNEL_ID
        )
        self.logger.info("ValorantStats cog initialized.")

    async def save_match_and_clan(self, data: dict, match_uuid: Optional[str] = None):
        if not hasattr(self.bot, 'pool') or self.bot.pool is None:
            self.logger.error("Database pool is not initialized on the bot. Cannot save match data.")
            return

        try:
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
                        ON CONFLICT (match_uuid) DO UPDATE SET
                            map = EXCLUDED.map,
                            mode = EXCLUDED.mode,
                            team1_score = EXCLUDED.team1_score,
                            team2_score = EXCLUDED.team2_score,
                            round_count = EXCLUDED.round_count
                        RETURNING id
                        """,
                        match_uuid, map_name, mode, team1_score, team2_score, round_count
                    )

                    self.logger.info(f"매치 데이터 저장/업데이트됨: UUID={match_uuid} -> ID={match_id}")

                    rows = await conn.fetch("SELECT discord_id, riot_id FROM registrations")
                    riot_to_discord = {row['riot_id']: row['discord_id'] for row in rows}

                    for p in data.get("players", []):
                        riot_id = p.get("name")
                        discord_id = riot_to_discord.get(riot_id)

                        try:
                            acs = p.get("acs")
                            score = p.get("score")
                            kills = p.get("kills")
                            deaths = p.get("deaths")
                            assists = p.get("assists")
                            plus_minus = p.get("plus_minus")
                            kd_ratio = p.get("kd_ratio")
                            dda = p.get("dda")
                            adr = p.get("adr")
                            hs_pct = p.get("hs_pct")
                            kast_pct = p.get("kast_pct")
                            fk = p.get("fk")
                            fd = p.get("fd")
                            mk = p.get("mk")
                            acs_bonus = p.get("acs_bonus")
                            round_win_points = p.get("round_win_points")
                            total_points = p.get("total_points")

                            await conn.execute(
                                """
                                INSERT INTO clan(match_id, discord_id, riot_id, name, agent, team, tier,
                                                 acs, score, kills, deaths, assists, plus_minus,
                                                 kd_ratio, dda, adr, hs_pct, kast_pct, fk, fd, mk,
                                                 acs_bonus, round_win_points, total_points)
                                VALUES ($1, $2, $3, $4, $5, $6, $7,
                                        $8, $9, $10, $11, $12, $13,
                                        $14, $15, $16, $17, $18, $19, $20, $21,
                                        $22, $23, $24)
                                ON CONFLICT (match_id, riot_id) DO UPDATE SET
                                    discord_id = EXCLUDED.discord_id,
                                    agent = EXCLUDED.agent,
                                    team = EXCLUDED.team,
                                    tier = EXCLUDED.tier,
                                    acs = EXCLUDED.acs,
                                    score = EXCLUDED.score,
                                    kills = EXCLUDED.kills,
                                    deaths = EXCLUDED.deaths,
                                    assists = EXCLUDED.assists,
                                    plus_minus = EXCLUDED.plus_minus,
                                    kd_ratio = EXCLUDED.kd_ratio,
                                    dda = EXCLUDED.dda,
                                    adr = EXCLUDED.adr,
                                    hs_pct = EXCLUDED.hs_pct,
                                    kast_pct = EXCLUDED.kast_pct,
                                    fk = EXCLUDED.fk,
                                    fd = EXCLUDED.fd,
                                    mk = EXCLUDED.mk,
                                    acs_bonus = EXCLUDED.acs_bonus,
                                    round_win_points = EXCLUDED.round_win_points,
                                    total_points = EXCLUDED.total_points
                                """,
                                match_id, discord_id, riot_id, riot_id, p.get("agent"), p.get("team"), p.get("tier"),
                                acs, score, kills, deaths, assists, plus_minus,
                                kd_ratio, dda, adr, hs_pct, kast_pct, fk,
                                fd, mk, acs_bonus, round_win_points, total_points
                            )
                            if discord_id is not None:
                                self.logger.debug(f"클랜 플레이어 데이터 저장/업데이트됨: {riot_id} (Discord ID: {discord_id}) for match {match_uuid}")
                            else:
                                self.logger.debug(f"클랜 플레이어 데이터 저장/업데이트됨: {riot_id} (Discord ID 없음) for match {match_uuid}")
                        except Exception as player_e:
                            self.logger.error(
                                f"Error saving player data for {riot_id} in match {match_uuid}: {player_e}\n{traceback.format_exc()}")
                            continue

        except asyncpg.exceptions.PostgresError as e:
            self.logger.error(f"Database error during transaction for match {match_uuid}: {e}\n{traceback.format_exc()}")
            await self.bot.get_channel(config.LOG_CHANNEL_ID).send(
                f"🚨 **데이터베이스 오류 발생!** 매치 데이터 저장 실패 (UUID: `{match_uuid}`): `{e}`"
            )
        except Exception as e:
            self.logger.critical(f"Unexpected error saving match {match_uuid}: {e}\n{traceback.format_exc()}")
            await self.bot.get_channel(config.LOG_CHANNEL_ID).send(
                f"🚨 **치명적인 오류 발생!** 매치 데이터 저장 중 예상치 못한 문제 (UUID: `{match_uuid}`): `{e}`"
            )


    @app_commands.command(name="통계", description="최근 매치 요약 통계를 확인합니다.")
    @app_commands.describe(count="최근 포함할 경기 수 (기본값 10, 최대 50)")
    async def mystats(self, interaction: discord.Interaction, count: Optional[int] = 10):
        await interaction.response.defer(ephemeral=True)

        if count is None or count <= 0:
            count = 10
        if count > 50:
            count = 50

        discord_id = interaction.user.id
        self.logger.info(f"{interaction.user.display_name} ({discord_id}) requested /통계 (last {count} matches).")

        if not hasattr(self.bot, 'pool') or self.bot.pool is None:
            self.logger.error("Database pool is not initialized on the bot. Cannot retrieve stats.")
            await interaction.followup.send("❌ 데이터베이스 연결을 초기화하지 못했습니다. 잠시 후 다시 시도해주세요.", ephemeral=True)
            return

        try:
            async with self.bot.pool.acquire() as conn:
                row = await conn.fetchrow(
                    f"""
                    SELECT
                        COALESCE(AVG(c.acs), 0.0)::float AS avg_acs,
                        COALESCE(AVG(c.kills), 0.0)::float AS avg_kills,
                        COALESCE(AVG(c.deaths), 0.0)::float AS avg_deaths,
                        COALESCE(AVG(c.assists), 0.0)::float AS avg_assists,
                        COALESCE(AVG(c.kd_ratio), 0.0)::float AS avg_kd,
                        COALESCE(AVG(c.adr), 0.0)::float AS avg_adr,
                        COALESCE(AVG(c.hs_pct), 0.0)::float AS avg_hs_pct,
                        COALESCE(AVG(c.total_points), 0.0)::float AS avg_points,
                        COUNT(c.id) AS matches_played
                    FROM (
                        SELECT *
                        FROM clan
                        WHERE discord_id = $1
                        ORDER BY id DESC
                        LIMIT $2
                    ) AS c
                    """,
                    discord_id, count
                )

                if not row or row['matches_played'] == 0:
                    await interaction.followup.send("기록된 매치가 없습니다. 먼저 매치를 등록하세요.", ephemeral=True)
                    self.logger.info(f"{discord_id} has no recorded matches.")
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
                self.logger.info(f"{interaction.user.display_name}'s stats response sent.")

        except asyncpg.exceptions.PostgresError as e:
            self.logger.error(f"Database error fetching stats for {discord_id}: {e}\n{traceback.format_exc()}")
            await interaction.followup.send("❌ 통계를 가져오는 중 데이터베이스 오류가 발생했습니다. 잠시 후 다시 시도해주세요.", ephemeral=True)
            await self.bot.get_channel(config.LOG_CHANNEL_ID).send(
                f"🚨 **데이터베이스 오류 발생!** 통계 가져오기 실패 (유저 ID: `{discord_id}`): `{e}`"
            )
        except Exception as e:
            self.logger.critical(f"Unexpected error fetching stats for {discord_id}: {e}\n{traceback.format_exc()}")
            await interaction.followup.send("❌ 통계를 가져오는 중 알 수 없는 오류가 발생했습니다.", ephemeral=True)
            await self.bot.get_channel(config.LOG_CHANNEL_ID).send(
                f"🚨 **치명적인 오류 발생!** 통계 가져오기 중 예상치 못한 문제 (유저 ID: `{discord_id}`): `{e}`"
            )


async def setup(bot: commands.Bot):
    await bot.add_cog(ValorantStats(bot))
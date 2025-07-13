import discord
from discord import app_commands
from discord.ext import commands
import subprocess
import json
import os
import urllib.parse
import uuid
from utils.logger import get_logger
from utils import config

class TrackerScraper(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.logger = get_logger("내전 스크레이퍼", bot=bot, discord_log_channel_id=config.LOG_CHANNEL_ID)

    @app_commands.command(
        name="내전등록",
        description="Valorant tracker.gg 매치 링크로 선수 점수 및 매치 포인트를 조회합니다."
    )
    @app_commands.describe(url="tracker.gg의 Valorant 매치 링크를 붙여넣으세요.")
    async def trackerscore(self, interaction: discord.Interaction, url: str):
        await interaction.response.defer(thinking=True)
        self.logger.info(f"{interaction.user} 님이 tracker.gg 매치 점수 조회 시도: {url}")

        try:
            script_path = os.path.join(os.path.dirname(__file__), "..", "scraper", "scraper.js")
            output_path = os.path.join(os.path.dirname(__file__), "..", "scraper", "screenshot.png")

            if os.path.exists(output_path):
                os.remove(output_path)
                self.logger.debug("기존 스크린샷 파일 삭제됨")

            result = subprocess.run(
                ["node", script_path, url],
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=120
            )

            stderr = result.stderr or ""
            stdout = result.stdout or ""

            if result.returncode != 0 or not stdout.strip():
                combined = (stderr + stdout).strip() or "(stdout 또는 stderr에 출력 없음)"
                self.logger.error(f"스크래핑 실패: {combined}")
                if os.path.exists(output_path):
                    file = discord.File(output_path, filename="screenshot.png")
                    await interaction.followup.send(f"❌ 스크래핑 실패:\n```\n{combined}\n```", file=file)
                else:
                    await interaction.followup.send(f"❌ 스크래핑 실패:\n```\n{combined}\n```")
                return

            try:
                data = json.loads(stdout)
                self.logger.info("스크래퍼 JSON 출력 성공적으로 파싱됨")
            except json.JSONDecodeError:
                self.logger.error("JSON 디코딩 실패")
                if os.path.exists(output_path):
                    file = discord.File(output_path, filename="screenshot.png")
                    await interaction.followup.send("❌ 스크래퍼에서 JSON 출력 파싱 실패", file=file)
                else:
                    await interaction.followup.send("❌ 스크래퍼에서 JSON 출력 파싱 실패")
                return

            match_uuid = data.get("match_id") or data.get("match_uuid") or str(uuid.uuid4())
            self.logger.debug(f"매치 UUID: {match_uuid}")

            # DB에서 이미 등록된 매치인지 확인
            try:
                async with self.bot.pool.acquire() as conn:
                    exists = await conn.fetchval(
                        "SELECT EXISTS(SELECT 1 FROM matches WHERE match_uuid=$1)", match_uuid
                    )
                if exists:
                    self.logger.warning(f"이미 등록된 매치 UUID: {match_uuid}")
                    await interaction.followup.send("⚠️ 이미 등록된 매치입니다.", ephemeral=True)
                    return
            except Exception as e:
                self.logger.error(f"DB 매치 존재 여부 확인 오류: {e}")
                await interaction.followup.send("❌ 매치 확인 중 데이터베이스 오류가 발생했습니다.", ephemeral=True)
                return

            valorant_stats_cog = self.bot.get_cog("ValorantStats")
            if valorant_stats_cog:
                await valorant_stats_cog.save_match_and_clan(data, match_uuid)
                self.logger.info(f"매치 및 플레이어 데이터 저장 완료: {match_uuid}")
            else:
                self.logger.warning("ValorantStats 코그를 찾을 수 없음")

            players = data.get("players", [])
            team1_score = data.get("team1_score", 0)
            team2_score = data.get("team2_score", 0)
            map_name = data.get("map", "알 수 없음")

            players.sort(key=lambda p: p.get("acs", 0), reverse=True)

            embed = discord.Embed(
                title=f"📊 매치 요약 - {map_name}",
                description=(
                    f"🔗 [Tracker.gg에서 보기]({url})\n\n"
                    f"🔴 팀 1: `{team1_score}` 라운드\n"
                    f"🔵 팀 2: `{team2_score}` 라운드\n\n"
                ),
                color=discord.Color.blurple()
            )

            for i, p in enumerate(players, start=1):
                riot_id = p.get("name", "Unknown#0000")
                encoded_riot_id = urllib.parse.quote(riot_id, safe='')
                profile_url = f"https://tracker.gg/valorant/profile/riot/{encoded_riot_id}/overview"

                discord_id = None
                try:
                    async with self.bot.pool.acquire() as conn:
                        discord_id = await conn.fetchval(
                            "SELECT discord_id FROM clan WHERE riot_id = $1 LIMIT 1", riot_id
                        )
                except Exception as e:
                    self.logger.error(f"DB에서 discord_id 조회 실패 ({riot_id}): {e}")

                medal_emojis = {1: "🥇", 2: "🥈", 3: "🥉"}
                medal = medal_emojis.get(i, f"{i}.")

                mention_text = f"<@{discord_id}>\n" if discord_id else ""
                riot_id_display = f"🕹️ [{riot_id}]({profile_url})"

                field_value = (
                    f"{medal}\n"
                    f"{mention_text}"
                    f"{riot_id_display}\n"
                    f"🎭 요원: {p.get('agent', '알 수 없음')} | 🧬 팀: {p.get('team', '알 수 없음')}\n"
                    f"📈 ACS: {p.get('acs', 0)} (+{p.get('acs_bonus', 0)} pts)\n"
                    f"🔄 라운드 승리: {p.get('round_win_points', 0)} pts\n"
                    f"🎯 총 포인트: {p.get('total_points', 0)}"
                )

                embed.add_field(
                    name="\u200b",
                    value=field_value,
                    inline=False
                )

            if os.path.exists(output_path):
                file = discord.File(output_path, filename="screenshot.png")
                await interaction.followup.send(embed=embed, file=file)
                self.logger.info("스크린샷과 함께 매치 요약 임베드 전송 완료")
            else:
                await interaction.followup.send(embed=embed)
                self.logger.info("매치 요약 임베드 전송 완료 (스크린샷 없음)")

        except subprocess.TimeoutExpired:
            self.logger.error("Puppeteer 스크립트 실행 시간 초과")
            await interaction.followup.send("❌ Puppeteer 스크립트 실행 시간 초과")
        except Exception as e:
            self.logger.error(f"스크래핑 중 예외 발생: {e}", exc_info=True)
            await interaction.followup.send(f"❌ 오류 발생: {str(e)}")

async def setup(bot: commands.Bot):
    await bot.add_cog(TrackerScraper(bot))

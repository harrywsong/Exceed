import discord
from discord import app_commands
from discord.ext import commands
import subprocess
import json
import os
import urllib.parse
import uuid
import asyncio  # Import asyncio for async subprocess execution
import traceback  # Import traceback for detailed error logging

from utils.logger import get_logger
from utils import config

# Import the is_registered check from clanstats.py
from cogs.clanstats import is_registered # Assuming clanstats.py is in the 'cogs' directory


class TrackerScraper(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.logger = get_logger("내전 스크레이퍼", bot=bot, discord_log_channel_id=config.LOG_CHANNEL_ID)
        self.logger.info("트래커 스크래퍼 기능이 초기화되었습니다.")

    @app_commands.command(
        name="내전등록",
        description="Valorant tracker.gg 매치 링크로 선수 점수 및 매치 포인트를 조회합니다."
    )
    @app_commands.describe(url="tracker.gg의 Valorant 매치 링크를 붙여넣으세요.")
    @app_commands.check(is_registered) # <--- ADDED THIS LINE
    async def trackerscore(self, interaction: discord.Interaction, url: str):
        await interaction.response.defer(thinking=True)
        self.logger.info(f"{interaction.user} 님이 tracker.gg 매치 점수 조회 시도: {url}")

        try:
            script_path = os.path.join(os.path.dirname(__file__), "..", "scraper", "scraper.js")
            output_path = os.path.join(os.path.dirname(__file__), "..", "scraper", "screenshot.png")

            # Clean up previous screenshot if it exists
            if os.path.exists(output_path):
                os.remove(output_path)
                self.logger.debug("기존 스크린샷 파일 삭제 완료.")

            # Attempt to extract match_uuid from the URL
            match_uuid = None
            try:
                # Expecting URL format like: https://tracker.gg/valorant/match/MATCH_UUID
                path_parts = url.split('/')
                # Get the last part, which should be the UUID
                if path_parts and len(path_parts[-1]) == 36 and '-' in path_parts[
                    -1]:  # Basic check for UUID length and format
                    match_uuid = path_parts[-1]
                else:
                    self.logger.warning(f"URL did not contain a valid UUID at the end: {url}")
            except Exception as e:
                self.logger.warning(f"Error extracting UUID from URL {url}: {e}")
                match_uuid = None  # Ensure it's None if parsing fails

            # Execute the Node.js scraper script asynchronously
            process = await asyncio.create_subprocess_exec(
                "node", script_path, url,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE
            )

            # Wait for the process to complete and capture its output
            stdout, stderr = await process.communicate()

            if stderr:
                self.logger.error(f"Puppeteer script stderr: {stderr.decode()}")

            if process.returncode != 0:
                self.logger.error(f"Puppeteer script exited with code {process.returncode}")
                await interaction.followup.send(
                    "❌ 매치 데이터를 가져오는 데 실패했습니다. 트래커 링크를 확인하거나 다시 시도해주세요.",
                    ephemeral=True
                )
                return

            json_output_str = stdout.decode().strip()
            self.logger.debug(f"Raw JSON output from scraper: {json_output_str}")

            # The scraper's console logs might appear before the JSON object.
            # Find the actual start of the JSON data.
            json_start_index = json_output_str.find('{')
            if json_start_index == -1:
                self.logger.error(f"No JSON object found in scraper output: {json_output_str}")
                await interaction.followup.send(
                    "❌ 스크레이퍼에서 유효한 JSON 데이터를 찾을 수 없습니다. 개발자에게 문의해주세요.",
                    ephemeral=True
                )
                return

            # Extract and parse the JSON part of the output
            json_data_str = json_output_str[json_start_index:]

            try:
                parsed_data = json.loads(json_data_str)
                self.logger.info("스크레이퍼 출력 JSON 파싱 성공.")
            except json.JSONDecodeError as e:
                self.logger.error(f"JSON 파싱 오류: {e}. Raw output: {json_output_str}\n{traceback.format_exc()}")
                await interaction.followup.send(
                    "❌ 스크레이퍼가 손상된 데이터를 반환했습니다. 개발자에게 문의해주세요.",
                    ephemeral=True
                )
                return

            # Pass the parsed data and match_uuid to the ValorantStats cog for saving
            valorant_stats_cog = self.bot.get_cog('ValorantStats')
            if valorant_stats_cog:
                await valorant_stats_cog.save_match_and_clan(parsed_data, match_uuid)
                self.logger.info(f"매치 데이터 ({match_uuid}) ValorantStats cog에 전달 완료.")
            else:
                self.logger.error("ValorantStats cog not found. Cannot save match data.")
                await interaction.followup.send(
                    "❌ 내부 오류: 통계 모듈을 찾을 수 없습니다. 관리자에게 문의해주세요.",
                    ephemeral=True
                )
                return

            # --- Construct and send the Discord embed ---
            embed = discord.Embed(
                title=f"📊 발로란트 매치 스코어 요약 - {parsed_data.get('map', '알 수 없음')}",
                description=(
                    f"**모드:** {parsed_data.get('mode', '알 수 없음')}\n"
                    f"**스코어:** Team 1 {parsed_data.get('team1_score', 0)} : {parsed_data.get('team2_score', 0)} Team 2\n"
                    f"**총 라운드:** {parsed_data.get('round_count', 0)}"
                ),
                color=discord.Color.blue()
            )

            for i, p in enumerate(parsed_data.get("players", [])):
                # Attempt to find Discord ID using Riot ID for mentioning
                discord_id = None
                if self.bot.pool:  # Ensure database pool is available
                    try:
                        async with self.bot.pool.acquire() as conn:
                            row = await conn.fetchrow(
                                "SELECT discord_id FROM registrations WHERE riot_id = $1",
                                p.get("name")
                            )
                            if row:
                                discord_id = row['discord_id']
                    except Exception as db_e:
                        self.logger.warning(f"Could not fetch discord_id for {p.get('name')}: {db_e}")

                medal = "🥇" if i == 0 else "🥈" if i == 1 else "🥉" if i == 2 else ""
                mention_text = f"<@{discord_id}>\n" if discord_id else ""
                riot_id = p.get('name', '알 수 없음')

                # Encode the Riot ID for the Tracker.gg profile URL
                encoded_riot_id = urllib.parse.quote_plus(riot_id)
                profile_url = f"https://tracker.gg/valorant/profile/riot/{encoded_riot_id}/overview"

                # Display Riot ID as a hyperlink to their Tracker.gg profile
                riot_id_display = f"[{riot_id}]({profile_url})"

                # Handle 'plus_minus' which might be a string like "+17" or "?"
                plus_minus_val = p.get('plus_minus', '0').replace('+', '')  # Remove '+' for conversion
                try:
                    plus_minus_int = int(plus_minus_val)
                    plus_minus_display = f"{'+' if plus_minus_int > 0 else ''}{plus_minus_int}"
                except ValueError:
                    plus_minus_display = plus_minus_val  # If not a number, keep original string (e.g., "?")

                field_value = (
                    f"{medal}\n"
                    f"{mention_text}"
                    f"{riot_id_display}\n"
                    f"🎭 요원: {p.get('agent', '알 수 없음')} | 🧬 팀: {p.get('team', '알 수 없음')}\n"
                    f"📈 ACS: {p.get('acs', 0)}    | 📊 KDA: {p.get('kills', 0)} / {p.get('deaths', 0)} / {p.get('assists', 0)} ({plus_minus_display})\n"
                    f"🔥 FK/FD: {p.get('fk', 0)} / {p.get('fd', 0)} | 🎯 헤드샷률: {p.get('hs_pct', 0)}%\n"
                    f"🌟 총 포인트: {p.get('total_points', 0)}"
                )

                embed.add_field(
                    name=f"[{p.get('tier', '?')}] {riot_id}",  # Player tier and Riot ID as field name
                    value=field_value,
                    inline=False
                )

            # Send the embed, with screenshot if available
            if os.path.exists(output_path):
                file = discord.File(output_path, filename="screenshot.png")
                await interaction.followup.send(embed=embed, file=file)
                self.logger.info("스크린샷과 함께 매치 요약 임베드 전송 완료")
            else:
                await interaction.followup.send(embed=embed)
                self.logger.info("매치 요약 임베드 전송 완료 (스크린샷 없음)")

        except subprocess.TimeoutExpired:
            self.logger.error("Puppeteer 스크립트 실행 시간 초과")
            await interaction.followup.send("❌ 스크레이퍼 실행 시간이 초과되었습니다. 다시 시도해주세요.", ephemeral=True)
        except FileNotFoundError:
            self.logger.error(f"Puppeteer script not found at {script_path}")
            await interaction.followup.send("❌ 스크레이퍼 파일을 찾을 수 없습니다. 봇 설정 오류입니다.", ephemeral=True)
        except Exception as e:
            self.logger.critical(f"Unexpected error in trackerscore command: {e}\n{traceback.format_exc()}")
            await interaction.followup.send(f"❌ 매치 데이터를 가져오는 중 알 수 없는 오류가 발생했습니다: `{e}`", ephemeral=True)
            # Notify the log channel for critical errors
            await self.bot.get_channel(config.LOG_CHANNEL_ID).send(
                f"🚨 **치명적인 오류 발생!** `/내전등록` 명령어 실행 중 예상치 못한 문제: `{e}`"
            )


async def setup(bot: commands.Bot):
    await bot.add_cog(TrackerScraper(bot))
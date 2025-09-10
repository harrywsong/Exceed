# cogs/casino_lottery.py
import discord
from discord.ext import commands
from discord import app_commands
import asyncio
import random

from utils.logger import get_logger
from utils import config


class LotteryCog(commands.Cog):
    """Lottery number matching game"""

    def __init__(self, bot):
        self.bot = bot
        self.logger = get_logger("복권", bot=bot, discord_log_channel_id=config.LOG_CHANNEL_ID)
        self.logger.info("복권 게임 시스템이 초기화되었습니다.")

    async def validate_game(self, interaction: discord.Interaction, bet: int):
        """Validate game using casino base"""
        casino_base = self.bot.get_cog('CasinoBaseCog')
        if not casino_base:
            return False, "카지노 시스템을 찾을 수 없습니다!"

        return await casino_base.validate_game_start(
            interaction, "lottery", bet, 50, 200
        )

    @app_commands.command(name="복권", description="번호 맞히기 복권")
    @app_commands.describe(
        bet="베팅 금액 (50-200)",
        numbers="선택할 번호 (1-10, 쉼표로 구분, 예: 1,3,7)"
    )
    async def lottery(self, interaction: discord.Interaction, bet: int, numbers: str):
        can_start, error_msg = await self.validate_game(interaction, bet)
        if not can_start:
            await interaction.response.send_message(error_msg, ephemeral=True)
            return

        try:
            chosen_numbers = [int(n.strip()) for n in numbers.split(",")]
            if len(chosen_numbers) != 3:
                await interaction.response.send_message("정확히 3개의 번호를 선택해주세요!", ephemeral=True)
                return
            if not all(1 <= n <= 10 for n in chosen_numbers):
                await interaction.response.send_message("번호는 1-10 사이만 가능합니다!", ephemeral=True)
                return
            if len(set(chosen_numbers)) != 3:
                await interaction.response.send_message("중복된 번호는 선택할 수 없습니다!", ephemeral=True)
                return
        except ValueError:
            await interaction.response.send_message("올바른 번호 형식이 아닙니다! (예: 1,3,7)", ephemeral=True)
            return

        coins_cog = self.bot.get_cog('CoinsCog')
        if not await coins_cog.remove_coins(interaction.user.id, bet, "lottery_bet", "Lottery bet"):
            await interaction.response.send_message("베팅 처리 실패!", ephemeral=True)
            return

        await interaction.response.defer()

        # Draw animation
        for i in range(3):
            embed = discord.Embed(
                title="🎫 복권 추첨 중...",
                description="🎱 번호를 뽑는 중입니다...",
                color=discord.Color.blue()
            )
            await interaction.edit_original_response(embed=embed)
            await asyncio.sleep(0.8)

        # Draw winning numbers
        winning_numbers = random.sample(range(1, 11), 3)
        matches = len(set(chosen_numbers) & set(winning_numbers))

        # Payout calculation
        payouts = {0: 0, 1: 0, 2: bet * 3, 3: bet * 50}
        payout = payouts[matches]

        if payout > 0:
            await coins_cog.add_coins(interaction.user.id, payout, "lottery_win", f"Lottery win: {matches} matches")

        if matches == 3:
            title = "🎉 대박! 전체 일치!"
            color = discord.Color.gold()
        elif matches == 2:
            title = "🎉 축하합니다! 2개 일치!"
            color = discord.Color.green()
        else:
            title = "💸 아쉽네요!"
            color = discord.Color.red()

        embed = discord.Embed(title=title, color=color)
        embed.add_field(
            name="🎯 결과",
            value=f"당첨번호: {sorted(winning_numbers)}\n선택번호: {sorted(chosen_numbers)}\n일치: {matches}개",
            inline=False
        )

        if payout > 0:
            embed.add_field(name="💰 상금", value=f"{payout:,} 코인", inline=False)
        else:
            embed.add_field(name="💸 손실", value=f"{bet:,} 코인", inline=False)

        new_balance = await coins_cog.get_user_coins(interaction.user.id)
        embed.add_field(name="현재 잔액", value=f"{new_balance:,} 코인", inline=False)

        await interaction.edit_original_response(embed=embed)
        self.logger.info(f"{interaction.user}가 복권에서 {matches}개 일치 ({bet} 코인)")


async def setup(bot):
    await bot.add_cog(LotteryCog(bot))
# cogs/casino_roulette_simple.py
import discord
from discord.ext import commands
from discord import app_commands
import asyncio
import random

from utils.logger import get_logger
from utils import config


class RouletteSimpleCog(commands.Cog):
    """Simple roulette game with single command"""

    def __init__(self, bot):
        self.bot = bot
        self.logger = get_logger("룰렛", bot=bot, discord_log_channel_id=config.LOG_CHANNEL_ID)

        # Roulette setup
        self.red_numbers = {1, 3, 5, 7, 9, 12, 14, 16, 18, 19, 21, 23, 25, 27, 30, 32, 34, 36}

        self.logger.info("룰렛 게임 시스템이 초기화되었습니다.")

    async def validate_game(self, interaction: discord.Interaction, bet: int, min_bet: int, max_bet: int):
        """Validate game using casino base"""
        casino_base = self.bot.get_cog('CasinoBaseCog')
        if not casino_base:
            return False, "카지노 시스템을 찾을 수 없습니다!"

        return await casino_base.validate_game_start(
            interaction, "roulette", bet, min_bet, max_bet
        )

    @app_commands.command(name="룰렛", description="룰렛 게임 (색깔 또는 숫자)")
    @app_commands.describe(
        bet="베팅 금액 (20-200)",
        bet_type="베팅 타입",
        value="베팅할 값 (색깔: red/black, 숫자: 0-36)"
    )
    @app_commands.choices(bet_type=[
        app_commands.Choice(name="색깔 (2배)", value="color"),
        app_commands.Choice(name="숫자 (36배)", value="number")
    ])
    async def roulette(self, interaction: discord.Interaction, bet: int, bet_type: str, value: str):
        # Validation based on bet type
        if bet_type == "color":
            if value.lower() not in ["red", "black"]:
                await interaction.response.send_message("색깔은 'red' 또는 'black'만 가능합니다!", ephemeral=True)
                return
            min_bet, max_bet = 20, 200
        else:  # number
            try:
                num_value = int(value)
                if not (0 <= num_value <= 36):
                    await interaction.response.send_message("숫자는 0-36 사이만 가능합니다!", ephemeral=True)
                    return
            except ValueError:
                await interaction.response.send_message("유효한 숫자를 입력해주세요!", ephemeral=True)
                return
            min_bet, max_bet = 10, 500

        can_start, error_msg = await self.validate_game(interaction, bet, min_bet, max_bet)
        if not can_start:
            await interaction.response.send_message(error_msg, ephemeral=True)
            return

        coins_cog = self.bot.get_cog('CoinsCog')
        if not await coins_cog.remove_coins(interaction.user.id, bet, "roulette_bet", "Roulette bet"):
            await interaction.response.send_message("베팅 처리 실패!", ephemeral=True)
            return

        await interaction.response.defer()

        # Spinning animation
        for i in range(8):
            temp_num = random.randint(0, 36)
            temp_color = "green" if temp_num == 0 else ("red" if temp_num in self.red_numbers else "black")
            color_emoji = {"red": "🔴", "black": "⚫", "green": "🟢"}[temp_color]

            embed = discord.Embed(
                title="🎡 룰렛 스핀 중...",
                description=f"{color_emoji} **{temp_num}** 🎡\n\n{'⚪' * (i % 4 + 1)} 스피닝... {'⚪' * (3 - i % 4)}",
                color=discord.Color.blue()
            )
            await interaction.edit_original_response(embed=embed)
            await asyncio.sleep(0.5)

        # Final result
        winning_number = random.randint(0, 36)
        winning_color = "green" if winning_number == 0 else ("red" if winning_number in self.red_numbers else "black")
        color_emoji = {"red": "🔴", "black": "⚫", "green": "🟢"}[winning_color]

        won = False
        payout = 0

        if bet_type == "color" and value.lower() == winning_color:
            won = True
            payout = bet * 2
        elif bet_type == "number" and int(value) == winning_number:
            won = True
            payout = bet * 36

        if won:
            await coins_cog.add_coins(interaction.user.id, payout, "roulette_win", f"Roulette win: {winning_number}")

        if won:
            embed = discord.Embed(
                title="🎉 승리!",
                description=f"{color_emoji} **{winning_number}** ({winning_color})\n\n{payout:,} 코인 획득!",
                color=discord.Color.green()
            )
        else:
            embed = discord.Embed(
                title="💸 아쉽네요!",
                description=f"{color_emoji} **{winning_number}** ({winning_color})\n예상: {value}\n\n{bet:,} 코인 손실",
                color=discord.Color.red()
            )

        new_balance = await coins_cog.get_user_coins(interaction.user.id)
        embed.add_field(name="현재 잔액", value=f"{new_balance:,} 코인", inline=False)

        await interaction.edit_original_response(embed=embed)
        self.logger.info(f"{interaction.user}가 룰렛에서 {bet} 코인 {'승리' if won else '패배'}")


async def setup(bot):
    await bot.add_cog(RouletteSimpleCog(bot))
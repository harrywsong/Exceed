# cogs/casino_coinflip.py
import discord
from discord.ext import commands
from discord import app_commands
import asyncio
import random

from utils.logger import get_logger
from utils import config


class CoinflipCog(commands.Cog):
    """Coinflip casino game"""

    def __init__(self, bot):
        self.bot = bot
        self.logger = get_logger("동전던지기", bot=bot, discord_log_channel_id=config.LOG_CHANNEL_ID)
        self.logger.info("동전던지기 게임 시스템이 초기화되었습니다.")

    async def validate_game(self, interaction: discord.Interaction, bet: int):
        """Validate game using casino base"""
        casino_base = self.bot.get_cog('CasinoBaseCog')
        if not casino_base:
            return False, "카지노 시스템을 찾을 수 없습니다!"

        return await casino_base.validate_game_start(
            interaction, "coinflip", bet, 5, 25
        )

    @app_commands.command(name="동전던지기", description="동전 던지기 게임")
    @app_commands.describe(
        bet="베팅 금액 (5-25)",
        choice="앞면(heads) 또는 뒷면(tails)"
    )
    @app_commands.choices(choice=[
        app_commands.Choice(name="앞면 (Heads)", value="heads"),
        app_commands.Choice(name="뒷면 (Tails)", value="tails")
    ])
    async def coinflip(self, interaction: discord.Interaction, bet: int, choice: str):
        # Validate game start
        can_start, error_msg = await self.validate_game(interaction, bet)
        if not can_start:
            await interaction.response.send_message(error_msg, ephemeral=True)
            return

        coins_cog = self.bot.get_cog('CoinsCog')
        if not await coins_cog.remove_coins(interaction.user.id, bet, "coinflip_bet", "Coinflip bet"):
            await interaction.response.send_message("베팅 처리 실패!", ephemeral=True)
            return

        await interaction.response.defer()

        # Flip animation
        flip_emojis = ["🪙", "⚪", "🟡", "⚫"]
        for i in range(4):
            embed = discord.Embed(
                title="🪙 동전 던지는 중...",
                description=f"{flip_emojis[i % len(flip_emojis)]} 빙글빙글...",
                color=discord.Color.blue()
            )
            await interaction.edit_original_response(embed=embed)
            await asyncio.sleep(0.5)

        # Final result
        result = random.choice(["heads", "tails"])
        won = result == choice

        if won:
            payout = bet * 2
            await coins_cog.add_coins(interaction.user.id, payout, "coinflip_win", f"Coinflip win: {result}")

        choice_korean = {"heads": "앞면", "tails": "뒷면"}
        result_korean = choice_korean[result]
        chosen_korean = choice_korean[choice]

        if won:
            embed = discord.Embed(
                title="🎉 승리!",
                description=f"결과: {result_korean}\n당신의 선택: {chosen_korean}\n\n{payout:,} 코인 획득!",
                color=discord.Color.green()
            )
        else:
            embed = discord.Embed(
                title="💸 아쉽네요!",
                description=f"결과: {result_korean}\n당신의 선택: {chosen_korean}\n\n{bet:,} 코인 손실",
                color=discord.Color.red()
            )

        new_balance = await coins_cog.get_user_coins(interaction.user.id)
        embed.add_field(name="현재 잔액", value=f"{new_balance:,} 코인", inline=False)

        await interaction.edit_original_response(embed=embed)
        self.logger.info(f"{interaction.user}가 동전던지기에서 {bet} 코인 {'승리' if won else '패배'}")


async def setup(bot):
    await bot.add_cog(CoinflipCog(bot))
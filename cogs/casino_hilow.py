# cogs/casino_hilow.py
import discord
from discord.ext import commands
from discord import app_commands
import asyncio
import random

from utils.logger import get_logger
from utils import config


class HiLowCog(commands.Cog):
    """Hi-Low dice game"""

    def __init__(self, bot):
        self.bot = bot
        self.logger = get_logger("하이로우", bot=bot, discord_log_channel_id=config.LOG_CHANNEL_ID)
        self.logger.info("하이로우 게임 시스템이 초기화되었습니다.")

    async def validate_game(self, interaction: discord.Interaction, bet: int):
        """Validate game using casino base"""
        casino_base = self.bot.get_cog('CasinoBaseCog')
        if not casino_base:
            return False, "카지노 시스템을 찾을 수 없습니다!"

        return await casino_base.validate_game_start(
            interaction, "hilow", bet, 10, 200
        )

    @app_commands.command(name="하이로우", description="7을 기준으로 높음/낮음 맞히기")
    @app_commands.describe(
        bet="베팅 금액 (10-200)",
        choice="7보다 높을지(high) 낮을지(low)"
    )
    @app_commands.choices(choice=[
        app_commands.Choice(name="높음 (8-12)", value="high"),
        app_commands.Choice(name="낮음 (2-6)", value="low")
    ])
    async def hilow(self, interaction: discord.Interaction, bet: int, choice: str):
        can_start, error_msg = await self.validate_game(interaction, bet)
        if not can_start:
            await interaction.response.send_message(error_msg, ephemeral=True)
            return

        coins_cog = self.bot.get_cog('CoinsCog')
        if not await coins_cog.remove_coins(interaction.user.id, bet, "hilow_bet", "Hi-Low bet"):
            await interaction.response.send_message("베팅 처리 실패!", ephemeral=True)
            return

        await interaction.response.defer()

        # Roll two dice
        dice_emojis = ["⚀", "⚁", "⚂", "⚃", "⚄", "⚅"]

        # Animation
        for i in range(4):
            temp_die1 = random.randint(1, 6)
            temp_die2 = random.randint(1, 6)
            temp_total = temp_die1 + temp_die2
            embed = discord.Embed(
                title="🎲 하이로우 - 굴리는 중...",
                description=f"{dice_emojis[temp_die1 - 1]} {dice_emojis[temp_die2 - 1]}\n합계: {temp_total}",
                color=discord.Color.blue()
            )
            await interaction.edit_original_response(embed=embed)
            await asyncio.sleep(0.6)

        # Final result
        die1 = random.randint(1, 6)
        die2 = random.randint(1, 6)
        total = die1 + die2

        won = False
        if choice == "high" and total > 7:
            won = True
        elif choice == "low" and total < 7:
            won = True
        elif total == 7:
            # Push - return bet
            await coins_cog.add_coins(interaction.user.id, bet, "hilow_push", "Hi-Low push (7)")

        if won:
            payout = bet * 2
            await coins_cog.add_coins(interaction.user.id, payout, "hilow_win", f"Hi-Low win: {total}")

        choice_korean = {"high": "높음", "low": "낮음"}

        if total == 7:
            embed = discord.Embed(
                title="🤝 무승부!",
                description=f"{dice_emojis[die1 - 1]} {dice_emojis[die2 - 1]}\n합계: **{total}** (정확히 7!)\n\n베팅 금액 반환",
                color=discord.Color.blue()
            )
        elif won:
            embed = discord.Embed(
                title="🎉 승리!",
                description=f"{dice_emojis[die1 - 1]} {dice_emojis[die2 - 1]}\n합계: **{total}** ({choice_korean[choice]} 맞음!)\n\n{payout:,} 코인 획득!",
                color=discord.Color.green()
            )
        else:
            embed = discord.Embed(
                title="💸 패배!",
                description=f"{dice_emojis[die1 - 1]} {dice_emojis[die2 - 1]}\n합계: **{total}** ({choice_korean[choice]} 틀림)\n\n{bet:,} 코인 손실",
                color=discord.Color.red()
            )

        new_balance = await coins_cog.get_user_coins(interaction.user.id)
        embed.add_field(name="현재 잔액", value=f"{new_balance:,} 코인", inline=False)

        await interaction.edit_original_response(embed=embed)
        self.logger.info(f"{interaction.user}가 하이로우에서 {bet} 코인 {'승리' if won else '패배' if total != 7 else '무승부'}")


async def setup(bot):
    await bot.add_cog(HiLowCog(bot))
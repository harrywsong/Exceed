# cogs/casino_dice_simple.py
import discord
from discord.ext import commands
from discord import app_commands
import asyncio
import random

from utils.logger import get_logger
from utils import config


class DiceGameCog(commands.Cog):
    """Simple dice guessing game"""

    def __init__(self, bot):
        self.bot = bot
        self.logger = get_logger("주사위", bot=bot, discord_log_channel_id=config.LOG_CHANNEL_ID)
        self.logger.info("주사위 게임 시스템이 초기화되었습니다.")

    def get_dice_visual(self, value):
        """Get visual representation of dice value"""
        dice_visuals = {
            1: "🎲[1]",
            2: "🎲[2]",
            3: "🎲[3]",
            4: "🎲[4]",
            5: "🎲[5]",
            6: "🎲[6]"
        }
        return dice_visuals.get(value, f"🎲[{value}]")

    def create_dice_display(self, die1, die2, total, rolling=False):
        """Create visual dice display"""
        if rolling:
            return f"{self.get_dice_visual(die1)} {self.get_dice_visual(die2)}\n🎯 합계: ❓"
        else:
            return f"{self.get_dice_visual(die1)} {self.get_dice_visual(die2)}\n🎯 **합계: {total}**"

    async def validate_game(self, interaction: discord.Interaction, bet: int):
        """Validate game using casino base"""
        casino_base = self.bot.get_cog('CasinoBaseCog')
        if not casino_base:
            return False, "카지노 시스템을 찾을 수 없습니다!"

        return await casino_base.validate_game_start(
            interaction, "dice_game", bet, 5, 200
        )

    @app_commands.command(name="주사위", description="주사위 합 맞히기 게임")
    @app_commands.describe(
        bet="베팅 금액 (5-200)",
        guess="예상 합계 (2-12)"
    )
    async def dice_game(self, interaction: discord.Interaction, bet: int, guess: int):
        if not (2 <= guess <= 12):
            await interaction.response.send_message("주사위 합은 2-12 사이만 가능합니다!", ephemeral=True)
            return

        can_start, error_msg = await self.validate_game(interaction, bet)
        if not can_start:
            await interaction.response.send_message(error_msg, ephemeral=True)
            return

        coins_cog = self.bot.get_cog('CoinsCog')
        if not await coins_cog.remove_coins(interaction.user.id, bet, "dice_game_bet", "Dice game bet"):
            await interaction.response.send_message("베팅 처리 실패!", ephemeral=True)
            return

        await interaction.response.defer()

        # Show initial bet
        embed = discord.Embed(
            title="🎲 주사위 게임",
            description=f"예상 합계: **{guess}**\n베팅 금액: **{bet:,}** 코인",
            color=discord.Color.blue()
        )
        await interaction.edit_original_response(embed=embed)
        await asyncio.sleep(1)

        # Rolling animation
        for i in range(6):
            die1 = random.randint(1, 6)
            die2 = random.randint(1, 6)
            embed = discord.Embed(
                title="🎲 주사위 굴리는 중...",
                description=f"🌟 굴리는 중... {i + 1}/6\n\n{self.create_dice_display(die1, die2, 0, rolling=True)}",
                color=discord.Color.blue()
            )
            await interaction.edit_original_response(embed=embed)
            await asyncio.sleep(0.7)

        # Final roll
        die1 = random.randint(1, 6)
        die2 = random.randint(1, 6)
        total = die1 + die2
        won = total == guess

        # Payout calculation (higher multiplier for harder guesses)
        payout_multipliers = {2: 35, 3: 17, 4: 11, 5: 8, 6: 6, 7: 5, 8: 6, 9: 8, 10: 11, 11: 17, 12: 35}

        if won:
            payout = bet * payout_multipliers[guess]
            await coins_cog.add_coins(interaction.user.id, payout, "dice_game_win", f"Dice win: {total}")

        if won:
            embed = discord.Embed(
                title="🎉 정확히 맞혔습니다!",
                color=discord.Color.green()
            )
            result_desc = f"{self.create_dice_display(die1, die2, total)}\n\n"
            result_desc += f"🎯 예상: **{guess}** ✅\n"
            result_desc += f"💎 배율: **{payout_multipliers[guess]}배**\n"
            result_desc += f"💰 획득: **{payout:,}** 코인"
        else:
            embed = discord.Embed(
                title="💸 아쉽네요!",
                color=discord.Color.red()
            )
            result_desc = f"{self.create_dice_display(die1, die2, total)}\n\n"
            result_desc += f"🎯 예상: **{guess}** ❌\n"
            result_desc += f"💸 손실: **{bet:,}** 코인"

        embed.description = result_desc

        new_balance = await coins_cog.get_user_coins(interaction.user.id)
        embed.add_field(name="💳 현재 잔액", value=f"{new_balance:,} 코인", inline=False)

        # Add odds table for reference
        odds_text = "**📊 배당표:**\n"
        odds_text += "2, 12: 35배 💎\n"
        odds_text += "3, 11: 17배 💰\n"
        odds_text += "4, 10: 11배 🏆\n"
        odds_text += "5, 9: 8배 ⭐\n"
        odds_text += "6, 8: 6배 💚\n"
        odds_text += "7: 5배 💙"

        embed.add_field(name="ℹ️ 참고", value=odds_text, inline=False)

        await interaction.edit_original_response(embed=embed)
        self.logger.info(f"{interaction.user}가 주사위에서 {bet} 코인 {'승리' if won else '패배'}")


async def setup(bot):
    await bot.add_cog(DiceGameCog(bot))
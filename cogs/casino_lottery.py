# cogs/casino_lottery.py - Updated for multi-server support
import discord
from discord.ext import commands
from discord import app_commands
import asyncio
import random

from utils.logger import get_logger
from utils.config import (
    is_feature_enabled,
    get_server_setting
)


class LotteryCog(commands.Cog):
    """Lottery number matching game - Multi-server aware"""

    def __init__(self, bot):
        self.bot = bot
        # FIX: The logger is now a global singleton, so we just get it by name.
        self.logger = get_logger("복권")
        self.logger.info("복권 게임 시스템이 초기화되었습니다.")

    def get_number_emoji(self, number):
        """Convert number to emoji representation"""
        number_emojis = {
            1: "1️⃣", 2: "2️⃣", 3: "3️⃣", 4: "4️⃣", 5: "5️⃣",
            6: "6️⃣", 7: "7️⃣", 8: "8️⃣", 9: "9️⃣", 10: "🔟"
        }
        return number_emojis.get(number, str(number))

    def create_lottery_balls_display(self, numbers, highlight=None):
        """Create visual lottery ball display"""
        ball_display = ""
        for num in sorted(numbers):
            emoji = self.get_number_emoji(num)
            if highlight and num in highlight:
                ball_display += f"✨{emoji}✨ "
            else:
                ball_display += f"{emoji} "
        return ball_display.strip()

    async def validate_game(self, interaction: discord.Interaction, bet: int):
        """Validate game using casino base"""
        casino_base = self.bot.get_cog('CasinoBaseCog')
        if not casino_base:
            return False, "카지노 시스템을 찾을 수 없습니다!"

        # Get server-specific limits
        min_bet = get_server_setting(interaction.guild.id, 'lottery_min_bet', 50)
        max_bet = get_server_setting(interaction.guild.id, 'lottery_max_bet', 200)

        return await casino_base.validate_game_start(
            interaction, "lottery", bet, min_bet, max_bet
        )

    @app_commands.command(name="복권", description="번호 맞히기 복권")
    @app_commands.describe(
        bet="베팅 금액",
        numbers="선택할 번호 (1-10, 쉼표로 구분, 예: 1,3,7)"
    )
    async def lottery(self, interaction: discord.Interaction, bet: int, numbers: str):
        # Check if casino games are enabled for this server
        if not interaction.guild or not is_feature_enabled(interaction.guild.id, 'casino_games'):
            await interaction.response.send_message("❌ 이 서버에서는 카지노 게임이 비활성화되어 있습니다!", ephemeral=True)
            return

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
        if not await coins_cog.remove_coins(interaction.user.id, interaction.guild.id, bet, "lottery_bet", "Lottery bet"):
            await interaction.response.send_message("베팅 처리 실패!", ephemeral=True)
            return

        await interaction.response.defer()

        # Show selected numbers
        embed = discord.Embed(
            title="🎫 복권 게임",
            description=f"선택한 번호:\n{self.create_lottery_balls_display(chosen_numbers)}",
            color=discord.Color.blue()
        )
        embed.set_footer(text=f"Server: {interaction.guild.name}")
        await interaction.edit_original_response(embed=embed)
        await asyncio.sleep(1.5)

        # Draw animation with spinning effect
        for i in range(4):
            temp_numbers = random.sample(range(1, 11), 3)
            embed = discord.Embed(
                title="🎫 복권 추첨 중...",
                description=f"🎰 번호를 뽑는 중입니다...\n\n{self.create_lottery_balls_display(temp_numbers)}",
                color=discord.Color.blue()
            )
            embed.set_footer(text=f"Server: {interaction.guild.name}")
            await interaction.edit_original_response(embed=embed)
            await asyncio.sleep(0.8)

        # Draw winning numbers
        winning_numbers = random.sample(range(1, 11), 3)
        matches = set(chosen_numbers) & set(winning_numbers)
        match_count = len(matches)

        # Payout calculation - server configurable
        base_payouts = {0: 0, 1: 0, 2: 3, 3: 50}
        multiplier_modifier = get_server_setting(interaction.guild.id, 'lottery_multiplier', 1.0)
        payouts = {k: int(bet * v * multiplier_modifier) for k, v in base_payouts.items()}

        payout = payouts[match_count]

        if payout > 0:
            await coins_cog.add_coins(interaction.user.id, interaction.guild.id, payout, "lottery_win", f"Lottery win: {match_count} matches")

        if match_count == 3:
            title = "🎉 대박! 전체 일치!"
            color = discord.Color.gold()
        elif match_count == 2:
            title = "🎉 축하합니다! 2개 일치!"
            color = discord.Color.green()
        else:
            title = "💸 아쉽네요!"
            color = discord.Color.red()

        embed = discord.Embed(title=title, color=color)

        # Create visual result display
        result_text = f"**🏆 당첨번호:**\n{self.create_lottery_balls_display(winning_numbers, matches)}\n\n"
        result_text += f"**🎯 선택번호:**\n{self.create_lottery_balls_display(chosen_numbers, matches)}\n\n"

        if matches:
            result_text += f"**✨ 일치하는 번호:** {self.create_lottery_balls_display(list(matches))}\n"

        result_text += f"**📊 일치 개수:** {match_count}개"

        embed.add_field(
            name="🎲 추첨 결과",
            value=result_text,
            inline=False
        )

        if payout > 0:
            embed.add_field(name="💰 상금", value=f"{payout:,} 코인", inline=True)
        else:
            embed.add_field(name="💸 손실", value=f"{bet:,} 코인", inline=True)

        new_balance = await coins_cog.get_user_coins(interaction.user.id, interaction.guild.id)
        embed.add_field(name="💳 현재 잔액", value=f"{new_balance:,} 코인", inline=True)

        # Add payout table with server-specific multipliers
        payout_3 = int(50 * multiplier_modifier)
        payout_2 = int(3 * multiplier_modifier)
        embed.add_field(
            name="📋 배당표",
            value=f"3개 일치: {payout_3}배 💎\n2개 일치: {payout_2}배 💚\n1개 이하: 0배 💸",
            inline=False
        )

        embed.set_footer(text=f"Server: {interaction.guild.name}")
        await interaction.edit_original_response(embed=embed)
        # FIX: Add extra={'guild_id': ...} for multi-server logging context
        self.logger.info(
            f"{interaction.user}가 복권에서 {match_count}개 일치 ({bet} 코인)",
            extra={'guild_id': interaction.guild.id}
        )


async def setup(bot):
    await bot.add_cog(LotteryCog(bot))
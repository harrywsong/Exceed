# =============================================================================
# cogs/casino_hilow.py - Updated for multi-server support
# =============================================================================

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


class HiLowCog(commands.Cog):
    """Hi-Low dice game - Multi-server aware"""

    def __init__(self, bot):
        self.bot = bot
        self.logger = get_logger("하이로우", bot=bot)
        self.logger.info("하이로우 게임 시스템이 초기화되었습니다.")

    def get_dice_visual(self, value):
        """Get visual representation of dice value"""
        dice_visuals = {
            1: "🔴[1]",
            2: "🟠[2]",
            3: "🟡[3]",
            4: "🟢[4]",
            5: "🔵[5]",
            6: "🟣[6]"
        }
        return dice_visuals.get(value, f"🎲[{value}]")

    def create_dice_display(self, die1, die2, total, rolling=False):
        """Create visual dice display with total analysis"""
        dice_display = f"{self.get_dice_visual(die1)} {self.get_dice_visual(die2)}"

        if rolling:
            return f"{dice_display}\n🎯 합계: ❓"

        # Add visual indicator for hi/low
        if total > 7:
            indicator = "📈 HIGH"
            color_emoji = "🔥"
        elif total < 7:
            indicator = "📉 LOW"
            color_emoji = "❄️"
        else:
            indicator = "🎯 SEVEN"
            color_emoji = "⚡"

        return f"{dice_display}\n🎯 **합계: {total}** {color_emoji}\n{indicator}"

    async def validate_game(self, interaction: discord.Interaction, bet: int):
        """Validate game using casino base"""
        casino_base = self.bot.get_cog('CasinoBaseCog')
        if not casino_base:
            return False, "카지노 시스템을 찾을 수 없습니다!"

        # Get server-specific limits
        min_bet = get_server_setting(interaction.guild.id, 'hilow_min_bet', 10)
        max_bet = get_server_setting(interaction.guild.id, 'hilow_max_bet', 200)

        return await casino_base.validate_game_start(
            interaction, "hilow", bet, min_bet, max_bet
        )

    @app_commands.command(name="하이로우", description="7을 기준으로 높음/낮음 맞히기")
    @app_commands.describe(
        bet="베팅 금액",
        choice="7보다 높을지(high) 낮을지(low)"
    )
    @app_commands.choices(choice=[
        app_commands.Choice(name="📈 높음 (8-12)", value="high"),
        app_commands.Choice(name="📉 낮음 (2-6)", value="low")
    ])
    async def hilow(self, interaction: discord.Interaction, bet: int, choice: str):
        # Check if casino games are enabled for this server
        if not interaction.guild or not is_feature_enabled(interaction.guild.id, 'casino_games'):
            await interaction.response.send_message("❌ 이 서버에서는 카지노 게임이 비활성화되어 있습니다!", ephemeral=True)
            return

        can_start, error_msg = await self.validate_game(interaction, bet)
        if not can_start:
            await interaction.response.send_message(error_msg, ephemeral=True)
            return

        coins_cog = self.bot.get_cog('CoinsCog')
        if not await coins_cog.remove_coins(interaction.user.id, interaction.guild.id, bet, "hilow_bet", "Hi-Low bet"):
            await interaction.response.send_message("베팅 처리 실패!", ephemeral=True)
            return

        await interaction.response.defer()

        choice_display = {"high": "📈 높음 (8-12)", "low": "📉 낮음 (2-6)"}

        # Show bet information
        embed = discord.Embed(
            title="🎲 하이로우 게임",
            description=f"예상: **{choice_display[choice]}**\n베팅: **{bet:,}** 코인\n\n기준점: **7** ⚡",
            color=discord.Color.blue()
        )
        embed.set_footer(text=f"Server: {interaction.guild.name}")
        await interaction.edit_original_response(embed=embed)
        await asyncio.sleep(1.5)

        # Rolling animation
        for i in range(5):
            temp_die1 = random.randint(1, 6)
            temp_die2 = random.randint(1, 6)
            embed = discord.Embed(
                title="🎲 하이로우 - 굴리는 중...",
                description=f"🌀 굴리는 중... {i + 1}/5\n\n{self.create_dice_display(temp_die1, temp_die2, 0, rolling=True)}",
                color=discord.Color.blue()
            )
            embed.set_footer(text=f"Server: {interaction.guild.name}")
            await interaction.edit_original_response(embed=embed)
            await asyncio.sleep(0.7)

        # Final result
        die1 = random.randint(1, 6)
        die2 = random.randint(1, 6)
        total = die1 + die2

        won = False
        result_type = ""
        if choice == "high" and total > 7:
            won = True
            result_type = "win"
        elif choice == "low" and total < 7:
            won = True
            result_type = "win"
        elif total == 7:
            result_type = "push"
            # Push - return bet
            await coins_cog.add_coins(interaction.user.id, interaction.guild.id, bet, "hilow_push", "Hi-Low push (7)")
        else:
            result_type = "loss"

        if won:
            # Get server-specific payout multiplier
            payout_multiplier = get_server_setting(interaction.guild.id, 'hilow_payout', 2.0)
            payout = int(bet * payout_multiplier)
            await coins_cog.add_coins(interaction.user.id, interaction.guild.id, payout, "hilow_win", f"Hi-Low win: {total}")

        # Create result embed
        if total == 7:
            embed = discord.Embed(
                title="🤝 무승부!",
                color=discord.Color.blue()
            )
            result_desc = f"{self.create_dice_display(die1, die2, total)}\n\n"
            result_desc += f"🎯 예상: **{choice_display[choice]}**\n"
            result_desc += f"⚡ 정확히 **7**이 나왔습니다!\n"
            result_desc += f"💰 베팅 금액 **{bet:,} 코인** 반환"

        elif won:
            embed = discord.Embed(
                title="🎉 승리!",
                color=discord.Color.green()
            )
            payout_multiplier = get_server_setting(interaction.guild.id, 'hilow_payout', 2.0)
            result_desc = f"{self.create_dice_display(die1, die2, total)}\n\n"
            result_desc += f"🎯 예상: **{choice_display[choice]}** ✅\n"
            result_desc += f"💎 {payout_multiplier}배 배당!\n"
            result_desc += f"💰 획득: **{payout:,}** 코인"

        else:
            embed = discord.Embed(
                title="💸 패배!",
                color=discord.Color.red()
            )
            result_desc = f"{self.create_dice_display(die1, die2, total)}\n\n"
            result_desc += f"🎯 예상: **{choice_display[choice]}** ❌\n"
            result_desc += f"💸 손실: **{bet:,}** 코인"

        embed.description = result_desc

        new_balance = await coins_cog.get_user_coins(interaction.user.id, interaction.guild.id)
        embed.add_field(name="💳 현재 잔액", value=f"{new_balance:,} 코인", inline=False)

        # Add game rules
        payout_multiplier = get_server_setting(interaction.guild.id, 'hilow_payout', 2.0)
        rules_text = "**📋 게임 규칙:**\n"
        rules_text += f"📈 **높음**: 8-12 ({payout_multiplier}배)\n"
        rules_text += f"📉 **낮음**: 2-6 ({payout_multiplier}배)\n"
        rules_text += "⚡ **7**: 무승부 (환불)"

        embed.add_field(name="ℹ️ 참고", value=rules_text, inline=False)
        embed.set_footer(text=f"Server: {interaction.guild.name}")

        await interaction.edit_original_response(embed=embed)
        self.logger.info(f"{interaction.user}가 하이로우에서 {bet} 코인 {'승리' if won else '패배' if total != 7 else '무승부'} (Guild: {interaction.guild.id})")


async def setup(bot):
    await bot.add_cog(HiLowCog(bot))
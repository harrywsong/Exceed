# cogs/casino_coinflip.py - Updated for multi-server support
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


class CoinflipCog(commands.Cog):
    """Coinflip casino game - Multi-server aware"""

    def __init__(self, bot):
        self.bot = bot
        # FIX: The logger is now a global singleton, so we just get it by name.
        self.logger = get_logger("동전던지기")
        self.logger.info("동전던지기 게임 시스템이 초기화되었습니다.")

    async def validate_game(self, interaction: discord.Interaction, bet: int):
        """Validate game using casino base"""
        casino_base = self.bot.get_cog('CasinoBaseCog')
        if not casino_base:
            return False, "카지노 시스템을 찾을 수 없습니다!"

        # Get server-specific limits
        min_bet = get_server_setting(interaction.guild.id, 'coinflip_min_bet', 5)
        max_bet = get_server_setting(interaction.guild.id, 'coinflip_max_bet', 1000)

        return await casino_base.validate_game_start(
            interaction, "coinflip", bet, min_bet, max_bet
        )

    @app_commands.command(name="동전던지기", description="동전 던지기 게임")
    @app_commands.describe(
        bet="베팅 금액",
        choice="앞면(heads) 또는 뒷면(tails)"
    )
    @app_commands.choices(choice=[
        app_commands.Choice(name="앞면 (Heads)", value="heads"),
        app_commands.Choice(name="뒷면 (Tails)", value="tails")
    ])
    async def coinflip(self, interaction: discord.Interaction, bet: int, choice: str):
        # Check if casino games are enabled for this server
        if not interaction.guild or not is_feature_enabled(interaction.guild.id, 'casino_games'):
            await interaction.response.send_message("❌ 이 서버에서는 카지노 게임이 비활성화되어 있습니다!", ephemeral=True)
            return

        # Validate game start
        can_start, error_msg = await self.validate_game(interaction, bet)
        if not can_start:
            await interaction.response.send_message(error_msg, ephemeral=True)
            return

        coins_cog = self.bot.get_cog('CoinsCog')
        if not await coins_cog.remove_coins(interaction.user.id, interaction.guild.id, bet, "coinflip_bet", "Coinflip bet"):
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
            embed.set_footer(text=f"Server: {interaction.guild.name}")
            await interaction.edit_original_response(embed=embed)
            await asyncio.sleep(0.5)

        # Final result
        result = random.choice(["heads", "tails"])
        won = result == choice

        if won:
            # Get server-specific payout multiplier
            payout_multiplier = get_server_setting(interaction.guild.id, 'coinflip_payout', 2.0)
            payout = int(bet * payout_multiplier)
            await coins_cog.add_coins(interaction.user.id, interaction.guild.id, payout, "coinflip_win", f"Coinflip win: {result}")

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
                title="💸 패배!",
                description=f"결과: {result_korean}\n당신의 선택: {chosen_korean}\n\n{bet:,} 코인 손실",
                color=discord.Color.red()
            )

        new_balance = await coins_cog.get_user_coins(interaction.user.id, interaction.guild.id)
        embed.add_field(name="현재 잔액", value=f"{new_balance:,} 코인", inline=False)
        embed.set_footer(text=f"Server: {interaction.guild.name}")

        await interaction.edit_original_response(embed=embed)
        # FIX: Add extra={'guild_id': ...} for multi-server logging context
        self.logger.info(
            f"{interaction.user}가 동전던지기에서 {bet} 코인 {'승리' if won else '패배'}",
            extra={'guild_id': interaction.guild.id}
        )


async def setup(bot):
    await bot.add_cog(CoinflipCog(bot))
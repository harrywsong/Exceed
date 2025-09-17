# cogs/casino_slots.py - Updated for multi-server support
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


class SlotMachineCog(commands.Cog):
    """Classic slot machine game - Multi-server aware"""

    def __init__(self, bot):
        self.bot = bot
        self.logger = get_logger("슬롯머신", bot=bot)

        # Slot symbols with different rarities and payouts
        self.symbols = {
            '🍒': {'weight': 25, 'payout': 2, 'name': '체리'},
            '🍋': {'weight': 20, 'payout': 3, 'name': '레몬'},
            '🍊': {'weight': 20, 'payout': 3, 'name': '오렌지'},
            '🍇': {'weight': 15, 'payout': 5, 'name': '포도'},
            '🔔': {'weight': 10, 'payout': 8, 'name': '벨'},
            '⭐': {'weight': 7, 'payout': 15, 'name': '스타'},
            '💎': {'weight': 2, 'payout': 50, 'name': '다이아몬드'},
            '7️⃣': {'weight': 1, 'payout': 100, 'name': '럭키 7'},
        }

        # Create weighted symbol list for random selection
        self.symbol_pool = []
        for symbol, data in self.symbols.items():
            self.symbol_pool.extend([symbol] * data['weight'])

        self.logger.info("슬롯머신 게임 시스템이 초기화되었습니다.")

    async def validate_game(self, interaction: discord.Interaction, bet: int):
        """Validate game using casino base"""
        casino_base = self.bot.get_cog('CasinoBaseCog')
        if not casino_base:
            return False, "카지노 시스템을 찾을 수 없습니다!"

        # Get server-specific limits
        min_bet = get_server_setting(interaction.guild.id, 'slots_min_bet', 10)
        max_bet = get_server_setting(interaction.guild.id, 'slots_max_bet', 50)

        return await casino_base.validate_game_start(
            interaction, "slot_machine", bet, min_bet, max_bet
        )

    def spin_reels(self) -> tuple:
        """Spin the slot machine reels"""
        return (
            random.choice(self.symbol_pool),
            random.choice(self.symbol_pool),
            random.choice(self.symbol_pool)
        )

    def calculate_payout(self, reel1: str, reel2: str, reel3: str, bet: int, guild_id: int) -> tuple:
        """Calculate payout based on reel results with server-specific multipliers"""
        # Get server-specific payout modifiers
        payout_multiplier = get_server_setting(guild_id, 'slots_payout_multiplier', 1.0)
        pair_multiplier = get_server_setting(guild_id, 'slots_pair_multiplier', 1.0)

        # Three of a kind - full payout
        if reel1 == reel2 == reel3:
            base_multiplier = self.symbols[reel1]['payout']
            multiplier = int(base_multiplier * payout_multiplier)
            symbol_name = self.symbols[reel1]['name']
            return bet * multiplier, f"🎊 **잭팟! {symbol_name} 트리플!** `×{multiplier}`"

        # Two of a kind - partial payout
        elif reel1 == reel2 or reel2 == reel3 or reel1 == reel3:
            # Find the matching symbol
            if reel1 == reel2:
                symbol = reel1
            elif reel2 == reel3:
                symbol = reel2
            else:
                symbol = reel1

            symbol_name = self.symbols[symbol]['name']

            # Special case for lucky 7s and diamonds - still good payout for pairs
            if symbol in ['7️⃣', '💎']:
                multiplier = max(5, int((self.symbols[symbol]['payout'] // 3) * pair_multiplier))
                return bet * multiplier, f"✨ **{symbol_name} 페어!** `×{multiplier}`"
            elif symbol in ['⭐', '🔔']:
                multiplier = max(2, int((self.symbols[symbol]['payout'] // 4) * pair_multiplier))
                return bet * multiplier, f"🎯 **{symbol_name} 페어!** `×{multiplier}`"
            else:
                multiplier = 1.5 * pair_multiplier
                return int(bet * multiplier), f"🎲 **{symbol_name} 페어** `×{multiplier}`"

        # No match - lose bet
        else:
            return 0, "💸 **꽝!** 다음 기회에..."

    def create_slot_display(self, reel1: str, reel2: str, reel3: str, is_spinning: bool = False) -> str:
        """Create clean slot machine display without ASCII art"""
        if is_spinning:
            return f"🎰 **[ {reel1} | {reel2} | {reel3} ]** 🎰\n\n🔄 **스피닝 중...**"
        else:
            return f"🎰 **[ {reel1} | {reel2} | {reel3} ]** 🎰\n\n🎊 **결과 확정!**"

    def create_payout_table(self, guild_id: int) -> str:
        """Create simple single-column payout table with server-specific multipliers"""
        lines = []
        sorted_symbols = sorted(self.symbols.items(), key=lambda x: x[1]['payout'], reverse=True)

        # Get server multipliers
        payout_multiplier = get_server_setting(guild_id, 'slots_payout_multiplier', 1.0)
        pair_multiplier = get_server_setting(guild_id, 'slots_pair_multiplier', 1.0)

        for symbol, data in sorted_symbols:
            adjusted_payout = int(data['payout'] * payout_multiplier)
            lines.append(f"{symbol} = ×{adjusted_payout}")

        return "\n".join(lines) + f"\n\n💡 **페어는 더 낮은 배당** (×{pair_multiplier:.1f})"

    @app_commands.command(name="슬롯", description="클래식 슬롯머신 게임")
    @app_commands.describe(bet="베팅 금액")
    async def slot_machine(self, interaction: discord.Interaction, bet: int):
        # Check if casino games are enabled for this server
        if not interaction.guild or not is_feature_enabled(interaction.guild.id, 'casino_games'):
            await interaction.response.send_message("❌ 이 서버에서는 카지노 게임이 비활성화되어 있습니다!", ephemeral=True)
            return

        can_start, error_msg = await self.validate_game(interaction, bet)
        if not can_start:
            await interaction.response.send_message(error_msg, ephemeral=True)
            return

        coins_cog = self.bot.get_cog('CoinsCog')
        if not await coins_cog.remove_coins(interaction.user.id, interaction.guild.id, bet, "slot_machine_bet", "Slot machine bet"):
            await interaction.response.send_message("베팅 처리 실패!", ephemeral=True)
            return

        await interaction.response.defer()

        # Spinning animation with different frames
        spinning_symbols = ['⚡', '🌟', '💫', '✨']

        for i in range(4):
            spin_frame = [random.choice(spinning_symbols) for _ in range(3)]

            embed = discord.Embed(
                title="🎰 슬롯머신",
                description=self.create_slot_display(spin_frame[0], spin_frame[1], spin_frame[2], True),
                color=discord.Color.blue()
            )

            embed.add_field(
                name="💰 베팅",
                value=f"`{bet:,}` 코인",
                inline=True
            )

            embed.add_field(
                name="🎲 상태",
                value=f"릴 스피닝 중... `{i + 1}/4`",
                inline=True
            )

            embed.set_footer(text=f"Server: {interaction.guild.name}")
            await interaction.edit_original_response(embed=embed)
            await asyncio.sleep(0.7)

        # Final spin result
        reel1, reel2, reel3 = self.spin_reels()
        payout, result_text = self.calculate_payout(reel1, reel2, reel3, bet, interaction.guild.id)

        # Determine result color and title
        if payout == 0:
            color = discord.Color.red()
            title = "🎰 슬롯머신 - 아쉽네요!"
        elif payout >= bet * 20:
            color = discord.Color.gold()
            title = "🎰 슬롯머신 - 🔥 메가 잭팟! 🔥"
        elif payout >= bet * 10:
            color = discord.Color.orange()
            title = "🎰 슬롯머신 - 💎 대박! 💎"
        elif payout > bet * 3:
            color = discord.Color.green()
            title = "🎰 슬롯머신 - ⭐ 빅윈! ⭐"
        elif payout > bet:
            color = discord.Color.blue()
            title = "🎰 슬롯머신 - 🎯 승리!"
        else:
            color = discord.Color.purple()
            title = "🎰 슬롯머신 - 👍 소액 당첨"

        embed = discord.Embed(title=title, color=color, timestamp=discord.utils.utcnow())

        # Clean slot display - no code blocks
        embed.add_field(
            name="🎯 슬롯 결과",
            value=self.create_slot_display(reel1, reel2, reel3),
            inline=False
        )

        # Combine result and financial info
        result_info = f"{result_text}\n\n"

        if payout > 0:
            await coins_cog.add_coins(interaction.user.id, interaction.guild.id, payout, "slot_machine_win", f"Slot machine win: {reel1}{reel2}{reel3}")

            profit = payout - bet
            result_info += f"💰 **수익:** {payout:,} 코인\n"
            if profit > 0:
                result_info += f"📈 **순이익:** +{profit:,} 코인"
            else:
                result_info += f"📉 **순손실:** {profit:,} 코인"
        else:
            result_info += f"💸 **손실:** {bet:,} 코인"

        embed.add_field(
            name="📊 게임 결과",
            value=result_info,
            inline=False
        )

        # Balance and server-specific payout info
        new_balance = await coins_cog.get_user_coins(interaction.user.id, interaction.guild.id)

        balance_payout = f"🏦 **잔액:** {new_balance:,} 코인\n\n**배당표 (트리플):**\n{self.create_payout_table(interaction.guild.id)}"

        embed.add_field(
            name="💳 정보",
            value=balance_payout,
            inline=False
        )

        # Simple footer
        embed.set_footer(text=f"플레이어: {interaction.user.display_name} | Server: {interaction.guild.name}")

        await interaction.edit_original_response(embed=embed)

        result = "승리" if payout > 0 else "패배"
        self.logger.info(
            f"{interaction.user}가 슬롯머신에서 {bet} 코인 {result} (결과: {reel1}{reel2}{reel3}, 수익: {payout}) (Guild: {interaction.guild.id})")


async def setup(bot):
    await bot.add_cog(SlotMachineCog(bot))
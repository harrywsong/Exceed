# cogs/casino_slots.py
import discord
from discord.ext import commands
from discord import app_commands
import asyncio
import random
from typing import List

from utils.logger import get_logger
from utils import config


class SlotMachineView(discord.ui.View):
    """Enhanced slot machine with multiple paylines and bonus features"""

    def __init__(self, bot, user_id: int, bet: int):
        super().__init__(timeout=60)
        self.bot = bot
        self.user_id = user_id
        self.bet = bet
        self.game_over = False

        # Enhanced slot symbols with different rarities and bonuses
        self.symbols = {
            '🍒': {'weight': 25, 'payout': 2, 'name': 'Cherry'},
            '🍊': {'weight': 20, 'payout': 3, 'name': 'Orange'},
            '🍋': {'weight': 18, 'payout': 4, 'name': 'Lemon'},
            '🍇': {'weight': 15, 'payout': 5, 'name': 'Grape'},
            '🍎': {'weight': 10, 'payout': 8, 'name': 'Apple'},
            '💎': {'weight': 8, 'payout': 15, 'name': 'Diamond'},
            '⭐': {'weight': 3, 'payout': 25, 'name': 'Star'},
            '🎰': {'weight': 1, 'payout': 100, 'name': 'JACKPOT'}
        }

    def get_random_symbol(self) -> str:
        symbols = list(self.symbols.keys())
        weights = [self.symbols[s]['weight'] for s in symbols]
        return random.choices(symbols, weights=weights)[0]

    def calculate_payout(self, reels: List[str]) -> tuple[int, str]:
        """Calculate payout and return bonus message"""
        # Jackpot - three 🎰
        if reels[0] == reels[1] == reels[2] == '🎰':
            return self.bet * 100, "🎉 MEGA JACKPOT! 🎉"

        # Three of a kind
        if reels[0] == reels[1] == reels[2]:
            symbol = reels[0]
            multiplier = self.symbols[symbol]['payout']
            return self.bet * multiplier, f"🎯 Triple {self.symbols[symbol]['name']}!"

        # Two of a kind
        pairs = {}
        for symbol in reels:
            pairs[symbol] = pairs.get(symbol, 0) + 1

        for symbol, count in pairs.items():
            if count == 2:
                multiplier = max(1, self.symbols[symbol]['payout'] // 4)
                return self.bet * multiplier, f"✨ Double {self.symbols[symbol]['name']}"

        # Special combinations
        if '💎' in reels and '⭐' in reels:
            return self.bet * 3, "💫 Lucky Combo!"

        if all(s in ['🍒', '🍊', '🍋'] for s in reels):
            return self.bet * 2, "🍓 Fruit Salad!"

        return 0, "Better luck next time!"

    @discord.ui.button(label="🎰 SPIN", style=discord.ButtonStyle.primary, emoji="🎰")
    async def spin_slot(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("❌ 이건 당신의 게임이 아닙니다!", ephemeral=True)
            return

        if self.game_over:
            await interaction.response.send_message("❌ 게임이 이미 끝났습니다!", ephemeral=True)
            return

        await interaction.response.defer()

        coins_cog = self.bot.get_cog('CoinsCog')
        if not coins_cog:
            await interaction.followup.send("❌ 코인 시스템을 찾을 수 없습니다!", ephemeral=True)
            return

        # Check and deduct bet
        user_coins = await coins_cog.get_user_coins(self.user_id)
        if user_coins < self.bet:
            await interaction.followup.send(f"❌ 코인이 부족합니다! 필요: {self.bet}, 보유: {user_coins}")
            self.game_over = True
            return

        if not await coins_cog.remove_coins(self.user_id, self.bet, "slot_machine_bet", "Slot machine bet"):
            await interaction.followup.send("❌ 베팅 처리에 실패했습니다!")
            return

        # Spinning animation with 5 frames
        for i in range(5):
            temp_reels = [self.get_random_symbol() for _ in range(3)]
            embed = discord.Embed(
                title="🎰 슬롯 머신",
                description=f"┌─────────────┐\n│ {' '.join(temp_reels)} │\n└─────────────┘\n\n🎲 스피닝... {['⚪', '🟡', '🟠', '🔴', '🟣'][i]}",
                color=discord.Color.blue()
            )
            await interaction.edit_original_response(embed=embed, view=self)
            await asyncio.sleep(0.6)

        # Final result
        reels = [self.get_random_symbol() for _ in range(3)]
        payout, bonus_msg = self.calculate_payout(reels)

        if payout > 0:
            await coins_cog.add_coins(self.user_id, payout, "slot_machine_win", f"Slot win: {' '.join(reels)}")
            net_profit = payout - self.bet
            result_text = f"🎉 {bonus_msg}\n{payout} 코인 획득! (순이익: {net_profit:+,})"
            color = discord.Color.green() if payout >= self.bet * 10 else discord.Color.gold()
        else:
            result_text = f"💸 {bonus_msg}\n{self.bet} 코인 손실"
            color = discord.Color.red()

        new_balance = await coins_cog.get_user_coins(self.user_id)

        embed = discord.Embed(
            title="🎰 슬롯 머신 결과",
            description=f"┌─────────────┐\n│ {' '.join(reels)} │\n└─────────────┘\n\n{result_text}\n\n현재 잔액: {new_balance:,} 코인",
            color=color
        )

        # Add symbol guide
        embed.add_field(
            name="💰 배당표",
            value="🎰 잭팟 x100 | ⭐ x25 | 💎 x15 | 🍎 x8\n🍇 x5 | 🍋 x4 | 🍊 x3 | 🍒 x2",
            inline=False
        )

        button.disabled = True
        button.label = "게임 종료"
        self.game_over = True

        await interaction.edit_original_response(embed=embed, view=self)


class SlotsCog(commands.Cog):
    """Slot machine games"""

    def __init__(self, bot):
        self.bot = bot
        self.logger = get_logger("슬롯머신", bot=bot, discord_log_channel_id=config.LOG_CHANNEL_ID)
        self.logger.info("슬롯머신 시스템이 초기화되었습니다.")

    @app_commands.command(name="슬롯", description="향상된 슬롯 머신 게임을 플레이합니다.")
    @app_commands.describe(bet="베팅할 코인 수 (10-1000)")
    async def slot_machine(self, interaction: discord.Interaction, bet: int):
        if bet < 10 or bet > 1000:
            await interaction.response.send_message("❌ 베팅은 10~1000 코인 사이만 가능합니다.", ephemeral=True)
            return

        coins_cog = self.bot.get_cog('CoinsCog')
        if not coins_cog:
            await interaction.response.send_message("❌ 코인 시스템을 찾을 수 없습니다!", ephemeral=True)
            return

        user_coins = await coins_cog.get_user_coins(interaction.user.id)
        if user_coins < bet:
            await interaction.response.send_message(f"❌ 코인 부족! 필요: {bet:,}, 보유: {user_coins:,}", ephemeral=True)
            return

        view = SlotMachineView(self.bot, interaction.user.id, bet)

        embed = discord.Embed(
            title="🎰 슬롯 머신",
            description=f"베팅: {bet:,} 코인\n\n행운을 빌며 스핀 버튼을 눌러보세요!",
            color=discord.Color.blue()
        )
        embed.add_field(
            name="🎯 게임 규칙",
            value="• 3개 동일 심볼로 대박!\n• 2개 동일로도 소액 당첨\n• 특별 조합으로 보너스 획득",
            inline=False
        )

        await interaction.response.send_message(embed=embed, view=view)
        self.logger.info(f"{interaction.user}가 {bet} 코인으로 슬롯머신 시작")


async def setup(bot):
    await bot.add_cog(SlotsCog(bot))
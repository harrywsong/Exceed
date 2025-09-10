# cogs/casino_wheel_lottery.py
import discord
from discord.ext import commands
from discord import app_commands
import asyncio
import random
from typing import List, Dict

from utils.logger import get_logger
from utils import config


class WheelOfFortuneView(discord.ui.View):
    """Wheel of Fortune with various segments"""

    def __init__(self, bot, user_id: int, bet: int):
        super().__init__(timeout=60)
        self.bot = bot
        self.user_id = user_id
        self.bet = bet
        self.game_over = False

        # Wheel segments with different probabilities
        self.segments = [
            {"name": "BANKRUPT", "multiplier": 0, "weight": 3, "color": "⚫"},
            {"name": "x0.5", "multiplier": 0.5, "weight": 8, "color": "🔴"},
            {"name": "x1", "multiplier": 1, "weight": 15, "color": "🟡"},
            {"name": "x2", "multiplier": 2, "weight": 20, "color": "🟢"},
            {"name": "x3", "multiplier": 3, "weight": 15, "color": "🔵"},
            {"name": "x5", "multiplier": 5, "weight": 10, "color": "🟣"},
            {"name": "x10", "multiplier": 10, "weight": 5, "color": "🟠"},
            {"name": "x20", "multiplier": 20, "weight": 2, "color": "🏆"},
            {"name": "JACKPOT", "multiplier": 50, "weight": 1, "color": "💰"}
        ]

    def spin_wheel(self) -> Dict:
        """Spin the wheel and return winning segment"""
        segments = []
        weights = []

        for segment in self.segments:
            segments.append(segment)
            weights.append(segment["weight"])

        return random.choices(segments, weights=weights)[0]

    @discord.ui.button(label="🎡 스핀!", style=discord.ButtonStyle.primary, emoji="🎡")
    async def spin_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id or self.game_over:
            await interaction.response.send_message("❌ 권한이 없습니다!", ephemeral=True)
            return

        await interaction.response.defer()

        # Spinning animation showing different segments
        for i in range(10):
            temp_segment = random.choice(self.segments)
            embed = discord.Embed(
                title="🎡 행운의 바퀴",
                description=f"{temp_segment['color']} **{temp_segment['name']}**\n\n{'🌟' * (i % 3 + 1)} 스피닝... {'🌟' * (2 - i % 3)}",
                color=discord.Color.blue()
            )
            await interaction.edit_original_response(embed=embed, view=self)
            await asyncio.sleep(0.4)

        # Final result
        winning_segment = self.spin_wheel()
        payout = int(self.bet * winning_segment["multiplier"])

        # Award payout
        coins_cog = self.bot.get_cog('CoinsCog')
        if payout > 0 and coins_cog:
            await coins_cog.add_coins(self.user_id, payout, "wheel_win", f"Wheel win: {winning_segment['name']}")

        # Determine result message and color
        if winning_segment["name"] == "JACKPOT":
            title = "💰 JACKPOT!"
            result_text = f"🎊 잭팟 당첨! {payout:,} 코인 대박!"
            color = discord.Color.gold()
        elif winning_segment["name"] == "BANKRUPT":
            title = "💀 BANKRUPT!"
            result_text = f"파산! {self.bet:,} 코인 손실"
            color = discord.Color.dark_red()
        elif payout > self.bet:
            title = "🎉 승리!"
            net_profit = payout - self.bet
            result_text = f"{payout:,} 코인 획득!\n순이익: +{net_profit:,} 코인"
            color = discord.Color.green()
        elif payout == self.bet:
            title = "🤝 본전!"
            result_text = f"{payout:,} 코인 반환"
            color = discord.Color.blue()
        else:
            title = "💸 손실"
            loss = self.bet - payout
            result_text = f"{payout:,} 코인만 반환\n손실: -{loss:,} 코인"
            color = discord.Color.orange()

        embed = discord.Embed(title=title, color=color)
        embed.add_field(
            name="🎯 결과",
            value=f"{winning_segment['color']} **{winning_segment['name']}**",
            inline=False
        )
        embed.add_field(name="💰 결과", value=result_text, inline=False)

        # Show wheel layout
        wheel_display = " | ".join([f"{s['color']}{s['name']}" for s in self.segments])
        embed.add_field(name="🎡 바퀴 구성", value=wheel_display, inline=False)

        button.disabled = True
        button.label = "게임 종료"
        self.game_over = True

        if coins_cog:
            new_balance = await coins_cog.get_user_coins(self.user_id)
            embed.add_field(name="현재 잔액", value=f"{new_balance:,} 코인", inline=False)

        await interaction.edit_original_response(embed=embed, view=self)


class LotteryView(discord.ui.View):
    """Lottery game with number selection"""

    def __init__(self, bot, user_id: int, bet: int, numbers: List[int]):
        super().__init__(timeout=60)
        self.bot = bot
        self.user_id = user_id
        self.bet = bet
        self.player_numbers = sorted(numbers)
        self.game_over = False

    def draw_lottery(self) -> List[int]:
        """Draw 6 winning numbers from 1-45"""
        return sorted(random.sample(range(1, 46), 6))

    def calculate_matches(self, winning_numbers: List[int]) -> int:
        """Calculate number of matches"""
        return len(set(self.player_numbers) & set(winning_numbers))

    def get_payout_multiplier(self, matches: int) -> float:
        """Get payout multiplier based on matches"""
        payout_table = {
            6: 1000,  # Jackpot
            5: 100,  # Second prize
            4: 20,  # Third prize
            3: 5,  # Fourth prize
            2: 2,  # Small prize
            1: 0,  # No prize
            0: 0  # No prize
        }
        return payout_table.get(matches, 0)

    @discord.ui.button(label="🎫 추첨!", style=discord.ButtonStyle.success, emoji="🎫")
    async def draw_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id or self.game_over:
            await interaction.response.send_message("❌ 권한이 없습니다!", ephemeral=True)
            return

        await interaction.response.defer()

        # Drawing animation
        for i in range(6):
            temp_numbers = sorted(random.sample(range(1, 46), i + 1))
            embed = discord.Embed(
                title="🎫 복권 추첨 중...",
                description=f"**당첨번호**: {' '.join(map(str, temp_numbers))} {'?' * (6 - len(temp_numbers))}\n**선택번호**: {' '.join(map(str, self.player_numbers))}",
                color=discord.Color.blue()
            )
            await interaction.edit_original_response(embed=embed, view=self)
            await asyncio.sleep(0.8)

        # Final drawing
        winning_numbers = self.draw_lottery()
        matches = self.calculate_matches(winning_numbers)
        multiplier = self.get_payout_multiplier(matches)
        payout = int(self.bet * multiplier)

        # Award payout
        coins_cog = self.bot.get_cog('CoinsCog')
        if payout > 0 and coins_cog:
            await coins_cog.add_coins(self.user_id, payout, "lottery_win", f"Lottery win: {matches} matches")

        # Create result embed
        if matches >= 6:
            title = "🎊 JACKPOT!"
            color = discord.Color.gold()
        elif matches >= 4:
            title = "🏆 고액 당첨!"
            color = discord.Color.green()
        elif matches >= 2:
            title = "🎉 당첨!"
            color = discord.Color.blue()
        else:
            title = "💸 아쉽네요"
            color = discord.Color.red()

        embed = discord.Embed(title=title, color=color)

        # Show numbers with matches highlighted
        winning_display = []
        for num in winning_numbers:
            if num in self.player_numbers:
                winning_display.append(f"**{num}**")  # Bold for matches
            else:
                winning_display.append(str(num))

        embed.add_field(
            name="🎯 당첨번호",
            value=" ".join(winning_display),
            inline=False
        )
        embed.add_field(
            name="📝 선택번호",
            value=" ".join(map(str, self.player_numbers)),
            inline=False
        )

        # Show result
        match_prizes = {6: "잭팟", 5: "2등", 4: "3등", 3: "4등", 2: "5등"}
        if matches >= 2:
            prize_name = match_prizes[matches]
            net_profit = payout - self.bet
            result_text = f"🎯 {matches}개 일치 - {prize_name}!\n{payout:,} 코인 획득 (순이익: +{net_profit:,})"
        else:
            result_text = f"🎯 {matches}개 일치\n{self.bet:,} 코인 손실"

        embed.add_field(name="💰 결과", value=result_text, inline=False)

        # Show prize table
        embed.add_field(
            name="🏆 배당표",
            value="6개: 1000배 | 5개: 100배 | 4개: 20배\n3개: 5배 | 2개: 2배 | 1개: 꽝",
            inline=False
        )

        button.disabled = True
        button.label = "게임 종료"
        self.game_over = True

        if coins_cog:
            new_balance = await coins_cog.get_user_coins(self.user_id)
            embed.add_field(name="현재 잔액", value=f"{new_balance:,} 코인", inline=False)

        await interaction.edit_original_response(embed=embed, view=self)


class WheelLotteryCog(commands.Cog):
    """Wheel of Fortune and Lottery games"""

    def __init__(self, bot):
        self.bot = bot
        self.logger = get_logger("바퀴복권", bot=bot, discord_log_channel_id=config.LOG_CHANNEL_ID)
        self.logger.info("바퀴 및 복권 게임 시스템이 초기화되었습니다.")

    @app_commands.command(name="행운의바퀴", description="행운의 바퀴를 돌려 배수를 얻으세요!")
    @app_commands.describe(bet="베팅할 코인 수 (25-1500)")
    async def wheel_of_fortune(self, interaction: discord.Interaction, bet: int):
        if bet < 25 or bet > 1500:
            await interaction.response.send_message("❌ 베팅은 25-1500 코인 사이만 가능합니다.", ephemeral=True)
            return

        coins_cog = self.bot.get_cog('CoinsCog')
        if not coins_cog:
            await interaction.response.send_message("❌ 코인 시스템을 찾을 수 없습니다!", ephemeral=True)
            return

        user_coins = await coins_cog.get_user_coins(interaction.user.id)
        if user_coins < bet:
            await interaction.response.send_message(f"❌ 코인 부족! 필요: {bet:,}, 보유: {user_coins:,}", ephemeral=True)
            return

        # Deduct bet
        if not await coins_cog.remove_coins(interaction.user.id, bet, "wheel_bet", "Wheel of Fortune bet"):
            await interaction.response.send_message("❌ 베팅 처리 실패!", ephemeral=True)
            return

        view = WheelOfFortuneView(self.bot, interaction.user.id, bet)

        embed = discord.Embed(
            title="🎡 행운의 바퀴",
            description=f"베팅: {bet:,} 코인\n\n바퀴를 돌려 운명을 결정하세요!",
            color=discord.Color.purple()
        )

        # Show segments and their probabilities
        segments_text = []
        for segment in view.segments:
            if segment["multiplier"] == 0:
                segments_text.append(f"{segment['color']} {segment['name']}")
            else:
                segments_text.append(f"{segment['color']} {segment['name']}")

        embed.add_field(
            name="🎯 바퀴 구성",
            value=" | ".join(segments_text),
            inline=False
        )
        embed.add_field(
            name="💡 팁",
            value="높은 배수일수록 확률이 낮습니다!\n파산하면 모든 베팅을 잃습니다.",
            inline=False
        )

        await interaction.response.send_message(embed=embed, view=view)
        self.logger.info(f"{interaction.user}가 {bet} 코인으로 행운의바퀴 시작")

    @app_commands.command(name="복권", description="6개 번호를 선택해서 복권 게임을 플레이하세요!")
    @app_commands.describe(
        bet="베팅할 코인 수 (50-1000)",
        num1="첫 번째 번호 (1-45)", num2="두 번째 번호 (1-45)", num3="세 번째 번호 (1-45)",
        num4="네 번째 번호 (1-45)", num5="다섯 번째 번호 (1-45)", num6="여섯 번째 번호 (1-45)"
    )
    async def lottery(self, interaction: discord.Interaction, bet: int,
                      num1: int, num2: int, num3: int, num4: int, num5: int, num6: int):

        if bet < 50 or bet > 1000:
            await interaction.response.send_message("❌ 베팅은 50-1000 코인 사이만 가능합니다.", ephemeral=True)
            return

        numbers = [num1, num2, num3, num4, num5, num6]

        # Validate numbers
        if any(num < 1 or num > 45 for num in numbers):
            await interaction.response.send_message("❌ 모든 번호는 1-45 사이여야 합니다!", ephemeral=True)
            return

        if len(set(numbers)) != 6:
            await interaction.response.send_message("❌ 중복된 번호가 있습니다! 6개의 서로 다른 번호를 선택하세요.", ephemeral=True)
            return

        coins_cog = self.bot.get_cog('CoinsCog')
        if not coins_cog:
            await interaction.response.send_message("❌ 코인 시스템을 찾을 수 없습니다!", ephemeral=True)
            return

        user_coins = await coins_cog.get_user_coins(interaction.user.id)
        if user_coins < bet:
            await interaction.response.send_message(f"❌ 코인 부족! 필요: {bet:,}, 보유: {user_coins:,}", ephemeral=True)
            return

        # Deduct bet
        if not await coins_cog.remove_coins(interaction.user.id, bet, "lottery_bet", "Lottery ticket purchase"):
            await interaction.response.send_message("❌ 베팅 처리 실패!", ephemeral=True)
            return

        view = LotteryView(self.bot, interaction.user.id, bet, numbers)

        embed = discord.Embed(
            title="🎫 복권",
            description=f"복권 구입: {bet:,} 코인",
            color=discord.Color.gold()
        )

        embed.add_field(
            name="📝 선택 번호",
            value=" ".join(map(str, sorted(numbers))),
            inline=False
        )

        embed.add_field(
            name="🏆 당첨 기준",
            value="6개 일치: 잭팟 (1000배)\n5개 일치: 2등 (100배)\n4개 일치: 3등 (20배)\n3개 일치: 4등 (5배)\n2개 일치: 5등 (2배)",
            inline=False
        )

        embed.add_field(
            name="🎯 확률 정보",
            value="6개 일치: 1/8,145,060\n5개 일치: 1/35,724\n4개 일치: 1/733\n3개 일치: 1/45",
            inline=False
        )

        await interaction.response.send_message(embed=embed, view=view)
        self.logger.info(f"{interaction.user}가 {bet} 코인으로 복권 구입: {numbers}")

    @app_commands.command(name="빠른복권", description="랜덤 번호로 빠르게 복권을 구입합니다!")
    @app_commands.describe(bet="베팅할 코인 수 (50-1000)")
    async def quick_lottery(self, interaction: discord.Interaction, bet: int):
        if bet < 50 or bet > 1000:
            await interaction.response.send_message("❌ 베팅은 50-1000 코인 사이만 가능합니다.", ephemeral=True)
            return

        coins_cog = self.bot.get_cog('CoinsCog')
        if not coins_cog:
            await interaction.response.send_message("❌ 코인 시스템을 찾을 수 없습니다!", ephemeral=True)
            return

        user_coins = await coins_cog.get_user_coins(interaction.user.id)
        if user_coins < bet:
            await interaction.response.send_message(f"❌ 코인 부족! 필요: {bet:,}, 보유: {user_coins:,}", ephemeral=True)
            return

        # Generate random numbers
        numbers = sorted(random.sample(range(1, 46), 6))

        # Deduct bet
        if not await coins_cog.remove_coins(interaction.user.id, bet, "lottery_bet", "Quick lottery ticket"):
            await interaction.response.send_message("❌ 베팅 처리 실패!", ephemeral=True)
            return

        view = LotteryView(self.bot, interaction.user.id, bet, numbers)

        embed = discord.Embed(
            title="⚡ 빠른 복권",
            description=f"자동 선번: {bet:,} 코인",
            color=discord.Color.blue()
        )

        embed.add_field(
            name="🎲 자동 선택 번호",
            value=" ".join(map(str, numbers)),
            inline=False
        )

        embed.add_field(
            name="💫 행운을 빌어요!",
            value="컴퓨터가 선택한 행운의 번호입니다!",
            inline=False
        )

        await interaction.response.send_message(embed=embed, view=view)
        self.logger.info(f"{interaction.user}가 {bet} 코인으로 빠른복권: {numbers}")

    @app_commands.command(name="스크래치", description="스크래치 복권으로 즉석 당첨을 노려보세요!")
    @app_commands.describe(bet="베팅할 코인 수 (10-300)")
    async def scratch_card(self, interaction: discord.Interaction, bet: int):
        if bet < 10 or bet > 300:
            await interaction.response.send_message("❌ 베팅은 10-300 코인 사이만 가능합니다.", ephemeral=True)
            return

        coins_cog = self.bot.get_cog('CoinsCog')
        if not coins_cog:
            await interaction.response.send_message("❌ 코인 시스템을 찾을 수 없습니다!", ephemeral=True)
            return

        user_coins = await coins_cog.get_user_coins(interaction.user.id)
        if user_coins < bet:
            await interaction.response.send_message(f"❌ 코인 부족! 필요: {bet:,}, 보유: {user_coins:,}", ephemeral=True)
            return

        # Deduct bet
        if not await coins_cog.remove_coins(interaction.user.id, bet, "scratch_bet", "Scratch card purchase"):
            await interaction.response.send_message("❌ 베팅 처리 실패!", ephemeral=True)
            return

        await interaction.response.defer()

        # Create scratch card symbols
        symbols = ['🍒', '🍋', '🍊', '🍇', '⭐', '💎', '🎰', '🔔']

        # Generate 9 symbols with chances for matching
        scratch_symbols = []
        win_chance = random.random()

        if win_chance < 0.15:  # 15% chance to win
            # Create winning combination
            winning_symbol = random.choice(symbols)
            winning_count = random.choices([3, 4, 5], weights=[70, 25, 5])[0]  # How many matches

            for i in range(winning_count):
                scratch_symbols.append(winning_symbol)

            # Fill rest with random
            while len(scratch_symbols) < 9:
                filler = random.choice([s for s in symbols if s != winning_symbol])
                scratch_symbols.append(filler)
        else:
            # No winning combination
            for _ in range(9):
                scratch_symbols.append(random.choice(symbols))

        random.shuffle(scratch_symbols)

        # Scratching animation
        revealed = ['❓'] * 9
        for i in range(9):
            revealed[i] = scratch_symbols[i]

            embed = discord.Embed(
                title="🎫 스크래치 복권",
                description="긁어서 3개 이상 같은 심볼을 찾으세요!",
                color=discord.Color.green()
            )

            # Show 3x3 grid
            grid_text = ""
            for row in range(3):
                row_symbols = []
                for col in range(3):
                    idx = row * 3 + col
                    row_symbols.append(revealed[idx])
                grid_text += " ".join(row_symbols) + "\n"

            embed.add_field(name="🎯 스크래치 카드", value=grid_text, inline=False)
            embed.add_field(name="진행상황", value=f"긁은 부분: {i + 1}/9", inline=False)

            await interaction.edit_original_response(embed=embed)
            await asyncio.sleep(0.4)

        # Calculate winnings
        symbol_counts = {}
        for symbol in scratch_symbols:
            symbol_counts[symbol] = symbol_counts.get(symbol, 0) + 1

        # Find highest match count
        max_matches = max(symbol_counts.values()) if symbol_counts else 0
        winning_symbol = None
        for symbol, count in symbol_counts.items():
            if count == max_matches and count >= 3:
                winning_symbol = symbol
                break

        # Calculate payout
        payout = 0
        if max_matches >= 3:
            multipliers = {3: 2, 4: 5, 5: 10, 6: 20, 7: 50, 8: 100, 9: 500}
            payout = bet * multipliers.get(max_matches, 0)

        # Award payout
        if payout > 0:
            await coins_cog.add_coins(interaction.user.id, payout, "scratch_win",
                                      f"Scratch win: {max_matches}x {winning_symbol}")

        # Final result
        if payout > 0:
            title = "🎉 당첨!"
            result_text = f"{winning_symbol} {max_matches}개 일치!\n{payout:,} 코인 획득!"
            color = discord.Color.gold()
        else:
            title = "💸 아쉽네요"
            result_text = f"3개 이상 일치하는 심볼이 없습니다.\n{bet:,} 코인 손실"
            color = discord.Color.red()

        embed = discord.Embed(title=title, color=color)
        embed.add_field(name="🎯 최종 결과", value=grid_text, inline=False)
        embed.add_field(name="💰 결과", value=result_text, inline=False)
        embed.add_field(
            name="🏆 배당표",
            value="3개 일치: 2배 | 4개: 5배 | 5개: 10배\n6개: 20배 | 7개: 50배 | 8개: 100배 | 9개: 500배",
            inline=False
        )

        new_balance = await coins_cog.get_user_coins(interaction.user.id)
        embed.add_field(name="현재 잔액", value=f"{new_balance:,} 코인", inline=False)

        await interaction.edit_original_response(embed=embed)
        self.logger.info(f"{interaction.user}가 {bet} 코인으로 스크래치: {max_matches} matches")


async def setup(bot):
    await bot.add_cog(WheelLotteryCog(bot))
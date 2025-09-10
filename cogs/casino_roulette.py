# cogs/casino_roulette.py
import discord
from discord.ext import commands
from discord import app_commands
import asyncio
import random
from typing import Dict, List

from utils.logger import get_logger
from utils import config


class RouletteView(discord.ui.View):
    """European Roulette with multiple betting options"""

    def __init__(self, bot, user_id: int, bets: Dict[str, int]):
        super().__init__(timeout=120)
        self.bot = bot
        self.user_id = user_id
        self.bets = bets  # {"bet_type": amount}
        self.game_over = False

        # European roulette numbers (0-36)
        self.numbers = list(range(37))

        # Color mappings
        self.red_numbers = {1, 3, 5, 7, 9, 12, 14, 16, 18, 19, 21, 23, 25, 27, 30, 32, 34, 36}
        self.black_numbers = {2, 4, 6, 8, 10, 11, 13, 15, 17, 20, 22, 24, 26, 28, 29, 31, 33, 35}

    def spin_wheel(self) -> int:
        """Spin the roulette wheel"""
        return random.choice(self.numbers)

    def get_number_color(self, number: int) -> str:
        """Get color of a number"""
        if number == 0:
            return "green"
        elif number in self.red_numbers:
            return "red"
        else:
            return "black"

    def calculate_payouts(self, winning_number: int) -> Dict[str, int]:
        """Calculate all payouts for the winning number"""
        payouts = {}
        total_payout = 0

        for bet_type, bet_amount in self.bets.items():
            payout = 0

            # Straight up (single number)
            if bet_type.startswith("number_") and int(bet_type.split("_")[1]) == winning_number:
                payout = bet_amount * 36  # 35:1 + original bet

            # Color bets
            elif bet_type == "red" and self.get_number_color(winning_number) == "red":
                payout = bet_amount * 2
            elif bet_type == "black" and self.get_number_color(winning_number) == "black":
                payout = bet_amount * 2

            # Even/Odd
            elif bet_type == "even" and winning_number != 0 and winning_number % 2 == 0:
                payout = bet_amount * 2
            elif bet_type == "odd" and winning_number % 2 == 1:
                payout = bet_amount * 2

            # High/Low
            elif bet_type == "high" and 19 <= winning_number <= 36:
                payout = bet_amount * 2
            elif bet_type == "low" and 1 <= winning_number <= 18:
                payout = bet_amount * 2

            # Dozens
            elif bet_type == "first_dozen" and 1 <= winning_number <= 12:
                payout = bet_amount * 3
            elif bet_type == "second_dozen" and 13 <= winning_number <= 24:
                payout = bet_amount * 3
            elif bet_type == "third_dozen" and 25 <= winning_number <= 36:
                payout = bet_amount * 3

            # Columns
            elif bet_type == "first_column" and winning_number % 3 == 1 and winning_number != 0:
                payout = bet_amount * 3
            elif bet_type == "second_column" and winning_number % 3 == 2:
                payout = bet_amount * 3
            elif bet_type == "third_column" and winning_number % 3 == 0 and winning_number != 0:
                payout = bet_amount * 3

            if payout > 0:
                payouts[bet_type] = payout
                total_payout += payout

        return payouts, total_payout

    @discord.ui.button(label="🎡 스핀!", style=discord.ButtonStyle.danger, emoji="🎡")
    async def spin_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id or self.game_over:
            await interaction.response.send_message("❌ 권한이 없습니다!", ephemeral=True)
            return

        await interaction.response.defer()

        # Spinning animation
        for i in range(8):
            temp_number = random.randint(0, 36)
            temp_color = self.get_number_color(temp_number)
            color_emoji = {"red": "🔴", "black": "⚫", "green": "🟢"}[temp_color]

            embed = discord.Embed(
                title="🎡 룰렛 스핀 중...",
                description=f"{color_emoji} **{temp_number}** 🎡\n\n{'⚪' * (i % 4 + 1)} 스피닝... {'⚪' * (3 - i % 4)}",
                color=discord.Color.blue()
            )
            await interaction.edit_original_response(embed=embed, view=self)
            await asyncio.sleep(0.5)

        # Final result
        winning_number = self.spin_wheel()
        winning_color = self.get_number_color(winning_number)
        color_emoji = {"red": "🔴", "black": "⚫", "green": "🟢"}[winning_color]

        # Calculate payouts
        winning_bets, total_payout = self.calculate_payouts(winning_number)
        total_bet = sum(self.bets.values())

        # Award winnings
        coins_cog = self.bot.get_cog('CoinsCog')
        if total_payout > 0 and coins_cog:
            await coins_cog.add_coins(
                self.user_id,
                total_payout,
                "roulette_win",
                f"Roulette win on {winning_number}"
            )

        # Create result embed
        if total_payout > 0:
            net_profit = total_payout - total_bet
            title = "🎉 승리!"
            color = discord.Color.green()
            result_text = f"총 {total_payout:,} 코인 획득!\n순이익: {net_profit:+,} 코인"
        else:
            title = "💸 아쉽네요!"
            color = discord.Color.red()
            result_text = f"{total_bet:,} 코인 손실"

        embed = discord.Embed(title=title, color=color)
        embed.add_field(
            name="🎯 당첨 번호",
            value=f"{color_emoji} **{winning_number}** ({winning_color})",
            inline=False
        )

        # Show winning bets
        if winning_bets:
            bet_results = []
            for bet_type, payout in winning_bets.items():
                bet_name = bet_type.replace("_", " ").title()
                bet_results.append(f"✅ {bet_name}: {payout:,} 코인")
            embed.add_field(name="🏆 당첨 베팅", value="\n".join(bet_results), inline=False)

        embed.add_field(name="💰 결과", value=result_text, inline=False)

        if coins_cog:
            new_balance = await coins_cog.get_user_coins(self.user_id)
            embed.add_field(name="현재 잔액", value=f"{new_balance:,} 코인", inline=False)

        button.disabled = True
        button.label = "게임 종료"
        self.game_over = True

        await interaction.edit_original_response(embed=embed, view=self)


class RouletteCog(commands.Cog):
    """European Roulette Casino Game"""

    def __init__(self, bot):
        self.bot = bot
        self.logger = get_logger("룰렛", bot=bot, discord_log_channel_id=config.LOG_CHANNEL_ID)
        self.logger.info("룰렛 시스템이 초기화되었습니다.")

        # Active betting sessions
        self.betting_sessions = {}

    @app_commands.command(name="룰렛베팅", description="룰렛 베팅을 시작합니다.")
    async def start_roulette_betting(self, interaction: discord.Interaction):
        """Start a new roulette betting session"""
        user_id = interaction.user.id

        if user_id in self.betting_sessions:
            await interaction.response.send_message("❌ 이미 진행 중인 베팅이 있습니다! `/룰렛스핀`으로 완료하세요.", ephemeral=True)
            return

        # Initialize betting session
        self.betting_sessions[user_id] = {"bets": {}, "total": 0}

        embed = discord.Embed(
            title="🎡 룰렛 베팅",
            description="아래 명령어로 베팅하고 `/룰렛스핀`으로 게임을 시작하세요!",
            color=discord.Color.blue()
        )

        embed.add_field(
            name="🎯 베팅 옵션",
            value="• `/룰렛숫자` - 특정 숫자 (36배)\n• `/룰렛색깔` - 빨강/검정 (2배)\n• `/룰렛홀짝` - 홀수/짝수 (2배)\n• `/룰렛범위` - 높음/낮음 (2배)\n• `/룰렛그룹` - 12개씩 그룹 (3배)",
            inline=False
        )

        await interaction.response.send_message(embed=embed)
        self.logger.info(f"{interaction.user}가 룰렛 베팅 세션 시작")

    @app_commands.command(name="룰렛숫자", description="특정 숫자에 베팅합니다 (0-36)")
    @app_commands.describe(number="베팅할 숫자 (0-36)", amount="베팅 금액")
    async def bet_number(self, interaction: discord.Interaction, number: int, amount: int):
        user_id = interaction.user.id

        if user_id not in self.betting_sessions:
            await interaction.response.send_message("❌ 먼저 `/룰렛베팅`으로 게임을 시작하세요!", ephemeral=True)
            return

        if not (0 <= number <= 36):
            await interaction.response.send_message("❌ 숫자는 0-36 사이만 가능합니다!", ephemeral=True)
            return

        if amount < 10 or amount > 500:
            await interaction.response.send_message("❌ 베팅은 10-500 코인 사이만 가능합니다!", ephemeral=True)
            return

        coins_cog = self.bot.get_cog('CoinsCog')
        if not coins_cog:
            await interaction.response.send_message("❌ 코인 시스템 오류!", ephemeral=True)
            return

        user_coins = await coins_cog.get_user_coins(user_id)
        session_total = self.betting_sessions[user_id]["total"]

        if user_coins < session_total + amount:
            await interaction.response.send_message(f"❌ 코인 부족! 필요: {amount}, 사용가능: {user_coins - session_total}",
                                                    ephemeral=True)
            return

        # Add bet to session
        bet_key = f"number_{number}"
        self.betting_sessions[user_id]["bets"][bet_key] = amount
        self.betting_sessions[user_id]["total"] += amount

        await interaction.response.send_message(
            f"✅ 숫자 {number}에 {amount:,} 코인 베팅 완료!\n현재 총 베팅: {self.betting_sessions[user_id]['total']:,} 코인",
            ephemeral=True
        )

    @app_commands.command(name="룰렛색깔", description="빨강 또는 검정에 베팅합니다")
    @app_commands.describe(color="베팅할 색깔", amount="베팅 금액")
    @app_commands.choices(color=[
        app_commands.Choice(name="빨강", value="red"),
        app_commands.Choice(name="검정", value="black")
    ])
    async def bet_color(self, interaction: discord.Interaction, color: str, amount: int):
        user_id = interaction.user.id

        if user_id not in self.betting_sessions:
            await interaction.response.send_message("❌ 먼저 `/룰렛베팅`으로 게임을 시작하세요!", ephemeral=True)
            return

        if amount < 20 or amount > 2000:
            await interaction.response.send_message("❌ 색깔 베팅은 20-2000 코인 사이만 가능합니다!", ephemeral=True)
            return

        coins_cog = self.bot.get_cog('CoinsCog')
        if not coins_cog:
            return

        user_coins = await coins_cog.get_user_coins(user_id)
        session_total = self.betting_sessions[user_id]["total"]

        if user_coins < session_total + amount:
            await interaction.response.send_message(f"❌ 코인 부족!", ephemeral=True)
            return

        # Add bet to session
        self.betting_sessions[user_id]["bets"][color] = amount
        self.betting_sessions[user_id]["total"] += amount

        color_name = "빨강" if color == "red" else "검정"
        await interaction.response.send_message(
            f"✅ {color_name}에 {amount:,} 코인 베팅 완료!\n현재 총 베팅: {self.betting_sessions[user_id]['total']:,} 코인",
            ephemeral=True
        )

    @app_commands.command(name="룰렛홀짝", description="홀수 또는 짝수에 베팅합니다")
    @app_commands.describe(choice="홀수 또는 짝수", amount="베팅 금액")
    @app_commands.choices(choice=[
        app_commands.Choice(name="홀수", value="odd"),
        app_commands.Choice(name="짝수", value="even")
    ])
    async def bet_odd_even(self, interaction: discord.Interaction, choice: str, amount: int):
        user_id = interaction.user.id

        if user_id not in self.betting_sessions:
            await interaction.response.send_message("❌ 먼저 `/룰렛베팅`으로 게임을 시작하세요!", ephemeral=True)
            return

        if amount < 20 or amount > 2000:
            await interaction.response.send_message("❌ 홀짝 베팅은 20-2000 코인 사이만 가능합니다!", ephemeral=True)
            return

        coins_cog = self.bot.get_cog('CoinsCog')
        user_coins = await coins_cog.get_user_coins(user_id)
        session_total = self.betting_sessions[user_id]["total"]

        if user_coins < session_total + amount:
            await interaction.response.send_message("❌ 코인 부족!", ephemeral=True)
            return

        self.betting_sessions[user_id]["bets"][choice] = amount
        self.betting_sessions[user_id]["total"] += amount

        choice_name = "홀수" if choice == "odd" else "짝수"
        await interaction.response.send_message(
            f"✅ {choice_name}에 {amount:,} 코인 베팅 완료!",
            ephemeral=True
        )

    @app_commands.command(name="룰렛스핀", description="베팅을 완료하고 룰렛을 돌립니다!")
    async def spin_roulette(self, interaction: discord.Interaction):
        user_id = interaction.user.id

        if user_id not in self.betting_sessions:
            await interaction.response.send_message("❌ 진행 중인 베팅이 없습니다! `/룰렛베팅`으로 시작하세요.", ephemeral=True)
            return

        session = self.betting_sessions[user_id]
        if not session["bets"]:
            await interaction.response.send_message("❌ 베팅이 없습니다! 먼저 베팅하세요.", ephemeral=True)
            return

        coins_cog = self.bot.get_cog('CoinsCog')
        if not coins_cog:
            return

        # Deduct all bets
        total_bet = session["total"]
        if not await coins_cog.remove_coins(user_id, total_bet, "roulette_bet", "Roulette bets"):
            await interaction.response.send_message("❌ 베팅 처리 실패!", ephemeral=True)
            return

        # Create game view
        view = RouletteView(self.bot, user_id, session["bets"])

        # Show betting summary
        bet_summary = []
        for bet_type, amount in session["bets"].items():
            bet_name = bet_type.replace("_", " ").title()
            bet_summary.append(f"• {bet_name}: {amount:,} 코인")

        embed = discord.Embed(
            title="🎡 룰렛 게임",
            color=discord.Color.blue()
        )
        embed.add_field(
            name="💰 베팅 내역",
            value="\n".join(bet_summary) + f"\n\n**총 베팅: {total_bet:,} 코인**",
            inline=False
        )
        embed.add_field(
            name="🎯 게임 시작",
            value="스핀 버튼을 눌러 룰렛을 돌리세요!",
            inline=False
        )

        await interaction.response.send_message(embed=embed, view=view)

        # Clear session
        del self.betting_sessions[user_id]
        self.logger.info(f"{interaction.user}가 {total_bet} 코인으로 룰렛 스핀")


async def setup(bot):
    await bot.add_cog(RouletteCog(bot))
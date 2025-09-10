# cogs/casino_dice.py
import discord
from discord.ext import commands
from discord import app_commands
import asyncio
import random
from typing import List, Tuple

from utils.logger import get_logger
from utils import config


class CrapsView(discord.ui.View):
    """Craps dice game with come out and point phases"""

    def __init__(self, bot, user_id: int, bet: int):
        super().__init__(timeout=120)
        self.bot = bot
        self.user_id = user_id
        self.bet = bet
        self.game_over = False

        self.phase = "come_out"  # come_out or point
        self.point = None
        self.history = []

    def roll_dice(self) -> Tuple[int, int, int]:
        """Roll two dice and return individual dice and total"""
        die1 = random.randint(1, 6)
        die2 = random.randint(1, 6)
        total = die1 + die2
        return die1, die2, total

    def get_dice_emoji(self, value: int) -> str:
        """Get dice emoji for value"""
        dice_emojis = {1: "⚀", 2: "⚁", 3: "⚂", 4: "⚃", 5: "⚄", 6: "⚅"}
        return dice_emojis.get(value, "🎲")

    @discord.ui.button(label="🎲 주사위 굴리기!", style=discord.ButtonStyle.primary, emoji="🎲")
    async def roll_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id or self.game_over:
            await interaction.response.send_message("❌ 권한이 없습니다!", ephemeral=True)
            return

        await interaction.response.defer()

        # Rolling animation
        for i in range(4):
            temp_die1, temp_die2, temp_total = self.roll_dice()
            embed = discord.Embed(
                title="🎲 크랩스 - 굴리는 중...",
                description=f"{self.get_dice_emoji(temp_die1)} {self.get_dice_emoji(temp_die2)}\n\n합계: **{temp_total}**",
                color=discord.Color.blue()
            )
            await interaction.edit_original_response(embed=embed, view=self)
            await asyncio.sleep(0.6)

        # Final roll
        die1, die2, total = self.roll_dice()
        self.history.append((die1, die2, total))

        coins_cog = self.bot.get_cog('CoinsCog')
        result_text = ""
        payout = 0

        if self.phase == "come_out":
            if total in [7, 11]:
                # Natural win
                payout = self.bet * 2
                result_text = "🎉 자연수 승리! 패스 라인 승리!"
                self.game_over = True
            elif total in [2, 3, 12]:
                # Craps
                result_text = f"🎯 크랩스 ({total})! 패스 라인 패배!"
                self.game_over = True
            else:
                # Establish point
                self.point = total
                self.phase = "point"
                result_text = f"🎯 포인트 설정: {total}\n이제 7이 나오기 전에 {total}를 다시 굴리세요!"

        else:  # point phase
            if total == self.point:
                # Made the point
                payout = self.bet * 2
                result_text = f"🎉 포인트 달성! {total} 성공!"
                self.game_over = True
            elif total == 7:
                # Seven out
                result_text = "💀 세븐 아웃! 패스 라인 패배!"
                self.game_over = True
            else:
                result_text = f"계속 굴리세요! 목표: {self.point} (7 피하기)"

        # Award payout
        if payout > 0 and coins_cog:
            await coins_cog.add_coins(self.user_id, payout, "craps_win", f"Craps win: {total}")

        # Create result embed
        color = discord.Color.green() if payout > 0 else discord.Color.red() if self.game_over else discord.Color.blue()

        embed = discord.Embed(
            title=f"🎲 크랩스 - {'컴아웃' if self.phase == 'come_out' or self.game_over else '포인트'} 단계",
            color=color
        )

        embed.add_field(
            name="🎯 주사위 결과",
            value=f"{self.get_dice_emoji(die1)} {self.get_dice_emoji(die2)}\n\n**합계: {total}**",
            inline=False
        )

        if self.point and not self.game_over:
            embed.add_field(name="🎯 현재 포인트", value=str(self.point), inline=True)

        embed.add_field(name="📊 결과", value=result_text, inline=False)

        # Show history
        if len(self.history) > 1:
            history_text = " → ".join([str(h[2]) for h in self.history[-5:]])  # Last 5 rolls
            embed.add_field(name="📜 최근 기록", value=history_text, inline=False)

        if self.game_over:
            button.disabled = True
            button.label = "게임 종료"

            if coins_cog:
                new_balance = await coins_cog.get_user_coins(self.user_id)
                embed.add_field(name="💰 현재 잔액", value=f"{new_balance:,} 코인", inline=False)

        await interaction.edit_original_response(embed=embed, view=self)


class SicBoView(discord.ui.View):
    """Sic Bo (Chinese dice game) with multiple betting options"""

    def __init__(self, bot, user_id: int, bets: dict):
        super().__init__(timeout=90)
        self.bot = bot
        self.user_id = user_id
        self.bets = bets  # {bet_type: amount}
        self.game_over = False

    def roll_three_dice(self) -> Tuple[int, int, int, int]:
        """Roll three dice"""
        die1 = random.randint(1, 6)
        die2 = random.randint(1, 6)
        die3 = random.randint(1, 6)
        total = die1 + die2 + die3
        return die1, die2, die3, total

    def get_dice_emoji(self, value: int) -> str:
        dice_emojis = {1: "⚀", 2: "⚁", 3: "⚂", 4: "⚃", 5: "⚄", 6: "⚅"}
        return dice_emojis.get(value, "🎲")

    def calculate_payouts(self, dice: Tuple[int, int, int, int]) -> dict:
        """Calculate payouts for all bets"""
        die1, die2, die3, total = dice
        dice_list = [die1, die2, die3]
        payouts = {}

        for bet_type, bet_amount in self.bets.items():
            payout = 0

            # Small/Big bets
            if bet_type == "small" and 4 <= total <= 10:
                payout = bet_amount * 2
            elif bet_type == "big" and 11 <= total <= 17:
                payout = bet_amount * 2

            # Specific total bets
            elif bet_type.startswith("total_") and int(bet_type.split("_")[1]) == total:
                # Different payouts for different totals
                if total in [4, 17]:
                    payout = bet_amount * 62
                elif total in [5, 16]:
                    payout = bet_amount * 31
                elif total in [6, 15]:
                    payout = bet_amount * 18
                elif total in [7, 14]:
                    payout = bet_amount * 12
                elif total in [8, 13]:
                    payout = bet_amount * 8
                elif total in [9, 12]:
                    payout = bet_amount * 7
                elif total in [10, 11]:
                    payout = bet_amount * 6

            # Triple bets
            elif bet_type == "any_triple" and len(set(dice_list)) == 1:
                payout = bet_amount * 31
            elif bet_type.startswith("triple_") and len(set(dice_list)) == 1:
                specific_number = int(bet_type.split("_")[1])
                if dice_list[0] == specific_number:
                    payout = bet_amount * 181

            # Single number bets
            elif bet_type.startswith("single_"):
                number = int(bet_type.split("_")[1])
                count = dice_list.count(number)
                if count == 1:
                    payout = bet_amount * 2
                elif count == 2:
                    payout = bet_amount * 3
                elif count == 3:
                    payout = bet_amount * 4

            if payout > 0:
                payouts[bet_type] = payout

        return payouts

    @discord.ui.button(label="🎲 주사위 굴리기!", style=discord.ButtonStyle.danger, emoji="🎲")
    async def roll_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id or self.game_over:
            await interaction.response.send_message("❌ 권한이 없습니다!", ephemeral=True)
            return

        await interaction.response.defer()

        # Rolling animation
        for i in range(5):
            temp_dice = self.roll_three_dice()
            embed = discord.Embed(
                title="🎲 식보 - 굴리는 중...",
                description=f"{self.get_dice_emoji(temp_dice[0])} {self.get_dice_emoji(temp_dice[1])} {self.get_dice_emoji(temp_dice[2])}\n\n합계: **{temp_dice[3]}**",
                color=discord.Color.blue()
            )
            await interaction.edit_original_response(embed=embed, view=self)
            await asyncio.sleep(0.5)

        # Final roll
        dice = self.roll_three_dice()
        die1, die2, die3, total = dice

        # Calculate payouts
        winning_bets = self.calculate_payouts(dice)
        total_payout = sum(winning_bets.values())
        total_bet = sum(self.bets.values())

        # Award winnings
        coins_cog = self.bot.get_cog('CoinsCog')
        if total_payout > 0 and coins_cog:
            await coins_cog.add_coins(self.user_id, total_payout, "sicbo_win", f"Sic Bo win: {total}")

        # Create result embed
        if total_payout > 0:
            net_profit = total_payout - total_bet
            title = "🎉 승리!"
            color = discord.Color.green()
        else:
            title = "💸 아쉽네요!"
            color = discord.Color.red()

        embed = discord.Embed(title=title, color=color)

        embed.add_field(
            name="🎯 주사위 결과",
            value=f"{self.get_dice_emoji(die1)} {self.get_dice_emoji(die2)} {self.get_dice_emoji(die3)}\n\n**합계: {total}**",
            inline=False
        )

        # Show winning bets if any
        if winning_bets:
            win_text = []
            for bet_type, payout in winning_bets.items():
                bet_name = bet_type.replace("_", " ").title()
                win_text.append(f"✅ {bet_name}: {payout:,} 코인")
            embed.add_field(name="🏆 당첨 베팅", value="\n".join(win_text), inline=False)

        # Show result summary
        if total_payout > 0:
            result_text = f"총 {total_payout:,} 코인 획득!\n순이익: {total_payout - total_bet:+,} 코인"
        else:
            result_text = f"{total_bet:,} 코인 손실"

        embed.add_field(name="💰 결과", value=result_text, inline=False)

        button.disabled = True
        button.label = "게임 종료"
        self.game_over = True

        if coins_cog:
            new_balance = await coins_cog.get_user_coins(self.user_id)
            embed.add_field(name="현재 잔액", value=f"{new_balance:,} 코인", inline=False)

        await interaction.edit_original_response(embed=embed, view=self)


class DiceGamesCog(commands.Cog):
    """Various dice-based casino games"""

    def __init__(self, bot):
        self.bot = bot
        self.logger = get_logger("주사위게임", bot=bot, discord_log_channel_id=config.LOG_CHANNEL_ID)
        self.logger.info("주사위 게임 시스템이 초기화되었습니다.")

        # Active Sic Bo betting sessions
        self.sicbo_sessions = {}

    @app_commands.command(name="크랩스", description="아메리카 크랩스 게임을 플레이합니다.")
    @app_commands.describe(bet="패스 라인 베팅 (25-1000)")
    async def craps(self, interaction: discord.Interaction, bet: int):
        if bet < 25 or bet > 1000:
            await interaction.response.send_message("❌ 베팅은 25-1000 코인 사이만 가능합니다.", ephemeral=True)
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
        if not await coins_cog.remove_coins(interaction.user.id, bet, "craps_bet", "Craps pass line bet"):
            await interaction.response.send_message("❌ 베팅 처리 실패!", ephemeral=True)
            return

        view = CrapsView(self.bot, interaction.user.id, bet)

        embed = discord.Embed(
            title="🎲 크랩스",
            description=f"패스 라인 베팅: {bet:,} 코인\n\n**컴아웃 롤**: 7 또는 11은 승리, 2,3,12는 패배\n다른 숫자는 포인트가 됩니다!",
            color=discord.Color.blue()
        )
        embed.add_field(
            name="📋 게임 규칙",
            value="• 컴아웃: 7,11 승리 | 2,3,12 패배\n• 포인트: 7 전에 포인트 다시 굴리면 승리\n• 전통적인 카지노 크랩스 룰",
            inline=False
        )

        await interaction.response.send_message(embed=embed, view=view)
        self.logger.info(f"{interaction.user}가 {bet} 코인으로 크랩스 시작")

    @app_commands.command(name="식보베팅", description="식보(중국 주사위) 베팅을 시작합니다.")
    async def start_sicbo_betting(self, interaction: discord.Interaction):
        user_id = interaction.user.id

        if user_id in self.sicbo_sessions:
            await interaction.response.send_message("❌ 이미 진행 중인 식보 게임이 있습니다!", ephemeral=True)
            return

        self.sicbo_sessions[user_id] = {"bets": {}, "total": 0}

        embed = discord.Embed(
            title="🎲 식보 베팅",
            description="중국 전통 주사위 게임입니다!\n아래 명령어로 베팅하고 `/식보굴리기`로 게임하세요.",
            color=discord.Color.gold()
        )

        embed.add_field(
            name="🎯 베팅 옵션",
            value="• `/식보크기` - 작음(4-10)/큼(11-17) - 2배\n• `/식보합계` - 정확한 합계 - 6~62배\n• `/식보숫자` - 특정 숫자 나오기 - 2~4배\n• `/식보트리플` - 3개 동일/모든 트리플",
            inline=False
        )

        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="식보크기", description="작음(4-10) 또는 큼(11-17)에 베팅")
    @app_commands.describe(size="작음 또는 큼", amount="베팅 금액")
    @app_commands.choices(size=[
        app_commands.Choice(name="작음 (4-10)", value="small"),
        app_commands.Choice(name="큼 (11-17)", value="big")
    ])
    async def sicbo_size(self, interaction: discord.Interaction, size: str, amount: int):
        user_id = interaction.user.id

        if user_id not in self.sicbo_sessions:
            await interaction.response.send_message("❌ 먼저 `/식보베팅`으로 시작하세요!", ephemeral=True)
            return

        if amount < 50 or amount > 2000:
            await interaction.response.send_message("❌ 크기 베팅은 50-2000 코인만 가능합니다!", ephemeral=True)
            return

        coins_cog = self.bot.get_cog('CoinsCog')
        user_coins = await coins_cog.get_user_coins(user_id)
        session_total = self.sicbo_sessions[user_id]["total"]

        if user_coins < session_total + amount:
            await interaction.response.send_message("❌ 코인 부족!", ephemeral=True)
            return

        self.sicbo_sessions[user_id]["bets"][size] = amount
        self.sicbo_sessions[user_id]["total"] += amount

        size_name = "작음 (4-10)" if size == "small" else "큼 (11-17)"
        await interaction.response.send_message(
            f"✅ {size_name}에 {amount:,} 코인 베팅 완료!",
            ephemeral=True
        )

    @app_commands.command(name="식보합계", description="정확한 합계에 베팅합니다.")
    @app_commands.describe(total="예상 합계 (4-17)", amount="베팅 금액")
    async def sicbo_total(self, interaction: discord.Interaction, total: int, amount: int):
        user_id = interaction.user.id

        if user_id not in self.sicbo_sessions:
            await interaction.response.send_message("❌ 먼저 `/식보베팅`으로 시작하세요!", ephemeral=True)
            return

        if not (4 <= total <= 17):
            await interaction.response.send_message("❌ 합계는 4-17 사이만 가능합니다!", ephemeral=True)
            return

        if amount < 10 or amount > 200:
            await interaction.response.send_message("❌ 합계 베팅은 10-200 코인만 가능합니다!", ephemeral=True)
            return

        coins_cog = self.bot.get_cog('CoinsCog')
        user_coins = await coins_cog.get_user_coins(user_id)
        session_total = self.sicbo_sessions[user_id]["total"]

        if user_coins < session_total + amount:
            await interaction.response.send_message("❌ 코인 부족!", ephemeral=True)
            return

        bet_key = f"total_{total}"
        self.sicbo_sessions[user_id]["bets"][bet_key] = amount
        self.sicbo_sessions[user_id]["total"] += amount

        # Show payout odds
        payout_map = {4: 62, 5: 31, 6: 18, 7: 12, 8: 8, 9: 7, 10: 6, 11: 6, 12: 7, 13: 8, 14: 12, 15: 18, 16: 31,
                      17: 62}
        odds = payout_map[total]

        await interaction.response.send_message(
            f"✅ 합계 {total}에 {amount:,} 코인 베팅! (배당률: {odds}배)",
            ephemeral=True
        )

    @app_commands.command(name="식보숫자", description="특정 숫자가 나올 것에 베팅합니다.")
    @app_commands.describe(number="베팅할 숫자 (1-6)", amount="베팅 금액")
    async def sicbo_single(self, interaction: discord.Interaction, number: int, amount: int):
        user_id = interaction.user.id

        if user_id not in self.sicbo_sessions:
            await interaction.response.send_message("❌ 먼저 `/식보베팅`으로 시작하세요!", ephemeral=True)
            return

        if not (1 <= number <= 6):
            await interaction.response.send_message("❌ 숫자는 1-6만 가능합니다!", ephemeral=True)
            return

        if amount < 20 or amount > 800:
            await interaction.response.send_message("❌ 숫자 베팅은 20-800 코인만 가능합니다!", ephemeral=True)
            return

        coins_cog = self.bot.get_cog('CoinsCog')
        user_coins = await coins_cog.get_user_coins(user_id)
        session_total = self.sicbo_sessions[user_id]["total"]

        if user_coins < session_total + amount:
            await interaction.response.send_message("❌ 코인 부족!", ephemeral=True)
            return

        bet_key = f"single_{number}"
        self.sicbo_sessions[user_id]["bets"][bet_key] = amount
        self.sicbo_sessions[user_id]["total"] += amount

        await interaction.response.send_message(
            f"✅ 숫자 {number}에 {amount:,} 코인 베팅!\n1개: 2배 | 2개: 3배 | 3개: 4배",
            ephemeral=True
        )

    @app_commands.command(name="식보굴리기", description="베팅을 완료하고 주사위 3개를 굴립니다!")
    async def roll_sicbo(self, interaction: discord.Interaction):
        user_id = interaction.user.id

        if user_id not in self.sicbo_sessions:
            await interaction.response.send_message("❌ 진행 중인 식보 게임이 없습니다!", ephemeral=True)
            return

        session = self.sicbo_sessions[user_id]
        if not session["bets"]:
            await interaction.response.send_message("❌ 베팅이 없습니다! 먼저 베팅하세요.", ephemeral=True)
            return

        coins_cog = self.bot.get_cog('CoinsCog')
        if not coins_cog:
            return

        # Deduct all bets
        total_bet = session["total"]
        if not await coins_cog.remove_coins(user_id, total_bet, "sicbo_bet", "Sic Bo bets"):
            await interaction.response.send_message("❌ 베팅 처리 실패!", ephemeral=True)
            return

        # Create game view
        view = SicBoView(self.bot, user_id, session["bets"])

        # Show betting summary
        bet_summary = []
        for bet_type, amount in session["bets"].items():
            bet_name = bet_type.replace("_", " ").title()
            bet_summary.append(f"• {bet_name}: {amount:,} 코인")

        embed = discord.Embed(
            title="🎲 식보 게임",
            color=discord.Color.gold()
        )
        embed.add_field(
            name="💰 베팅 내역",
            value="\n".join(bet_summary) + f"\n\n**총 베팅: {total_bet:,} 코인**",
            inline=False
        )

        await interaction.response.send_message(embed=embed, view=view)

        # Clear session
        del self.sicbo_sessions[user_id]
        self.logger.info(f"{interaction.user}가 {total_bet} 코인으로 식보 플레이")


async def setup(bot):
    await bot.add_cog(DiceGamesCog(bot))
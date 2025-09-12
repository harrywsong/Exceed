# cogs/casino_blackjack.py
import discord
from discord.ext import commands
from discord import app_commands
import random
from typing import List, Dict

from utils.logger import get_logger
from utils import config


class BlackjackView(discord.ui.View):
    """Enhanced Blackjack with double down, insurance, and split"""

    def __init__(self, bot, user_id: int, bet: int):
        super().__init__(timeout=180)
        self.bot = bot
        self.user_id = user_id
        self.bet = bet
        self.game_over = False
        self.doubled_down = False
        self.insurance_bet = 0
        self.is_split = False
        self.current_hand = 0
        self.split_hands = []

        # Create and shuffle deck
        self.deck = self.create_deck()
        random.shuffle(self.deck)

        # Deal initial hands
        self.player_hand = [self.draw_card(), self.draw_card()]
        self.dealer_hand = [self.draw_card(), self.draw_card()]

        # Check for dealer ace (insurance option) - Fixed logic
        self.can_insure = self.dealer_hand[0]['rank'] == 'A'

        # Check for natural blackjack
        self.player_blackjack = self.calculate_hand_value(self.player_hand) == 21
        self.dealer_blackjack = self.calculate_hand_value(self.dealer_hand) == 21

        # If player has blackjack, end game immediately
        if self.player_blackjack:
            self.game_over = True

    def create_deck(self) -> List[Dict]:
        """Create multiple decks for more realistic play"""
        suits = ['♠️', '♥️', '♦️', '♣️']
        ranks = ['A', '2', '3', '4', '5', '6', '7', '8', '9', '10', 'J', 'Q', 'K']

        deck = []
        # Use 4 decks for more realistic casino experience
        for _ in range(4):
            for suit in suits:
                for rank in ranks:
                    value = 11 if rank == 'A' else (10 if rank in ['J', 'Q', 'K'] else int(rank))
                    deck.append({'rank': rank, 'suit': suit, 'value': value})

        return deck

    def draw_card(self) -> Dict:
        """Draw a card from deck"""
        if len(self.deck) < 10:  # Reshuffle if running low
            self.deck = self.create_deck()
            random.shuffle(self.deck)
        return self.deck.pop()

    def calculate_hand_value(self, hand: List[Dict]) -> int:
        """Calculate hand value with proper ace handling"""
        total = sum(card['value'] for card in hand)
        aces = sum(1 for card in hand if card['rank'] == 'A')

        while total > 21 and aces > 0:
            total -= 10
            aces -= 1

        return total

    def hand_to_string(self, hand: List[Dict], hide_first: bool = False) -> str:
        """Convert hand to display string"""
        if hide_first:
            return f"🔒 {hand[1]['rank']}{hand[1]['suit']}"
        return ' '.join(f"{card['rank']}{card['suit']}" for card in hand)

    def can_double_down(self) -> bool:
        """Check if player can double down"""
        current_hand = self.split_hands[self.current_hand] if self.is_split else self.player_hand
        return len(current_hand) == 2 and not self.doubled_down and not self.game_over

    def can_split(self) -> bool:
        """Check if player can split"""
        if self.is_split or len(self.player_hand) != 2 or self.game_over:
            return False

        # Can split if both cards have same rank or both are 10-value cards
        card1, card2 = self.player_hand
        return (card1['rank'] == card2['rank'] or
                (card1['value'] == 10 and card2['value'] == 10))

    async def create_embed(self, final: bool = False) -> discord.Embed:
        """Create game state embed"""
        if self.is_split:
            return await self.create_split_embed(final)

        player_value = self.calculate_hand_value(self.player_hand)
        dealer_value = self.calculate_hand_value(self.dealer_hand)

        if self.player_blackjack and self.dealer_blackjack:
            title = "🤝 양쪽 블랙잭 - Push! (무승부)"
            color = discord.Color.blue()
        elif self.player_blackjack and not self.dealer_blackjack:
            title = "🎊 블랙잭!"
            color = discord.Color.gold()
        elif player_value > 21:
            title = "💥 버스트!"
            color = discord.Color.red()
        elif final and dealer_value > 21:
            title = "🎉 딜러 버스트 - 승리!"
            color = discord.Color.green()
        elif final:
            if player_value > dealer_value:
                title = "🏆 승리!"
                color = discord.Color.green()
            elif player_value < dealer_value:
                title = "😞 딜러 승리"
                color = discord.Color.red()
            else:
                title = "🤝 Push (무승부)"
                color = discord.Color.blue()
        else:
            title = "🃏 블랙잭"
            color = discord.Color.blue()

        embed = discord.Embed(title=title, color=color)

        # Dealer hand
        dealer_display = self.hand_to_string(self.dealer_hand, not final and not self.game_over)
        dealer_value_text = f"({dealer_value})" if final or self.game_over else "(?)"
        embed.add_field(
            name=f"🎩 딜러 {dealer_value_text}",
            value=dealer_display,
            inline=False
        )

        # Player hand
        player_display = self.hand_to_string(self.player_hand)
        hand_type = " (Blackjack)" if self.player_blackjack else " (Soft)" if any(
            c['rank'] == 'A' for c in self.player_hand) and player_value <= 21 else ""
        embed.add_field(
            name=f"👤 플레이어 ({player_value}){hand_type}",
            value=player_display,
            inline=False
        )

        # Game info
        bet_info = f"베팅: {self.bet:,} 코인"
        if self.doubled_down:
            bet_info += f" (더블다운: {self.bet * 2:,})"
        if self.insurance_bet > 0:
            bet_info += f" | 보험: {self.insurance_bet:,}"

        embed.add_field(name="💰 베팅 정보", value=bet_info, inline=False)

        return embed

    async def create_split_embed(self, final: bool = False) -> discord.Embed:
        """Create embed for split hands"""
        embed = discord.Embed(title="✂️ 스플릿 게임", color=discord.Color.purple())

        # Dealer hand
        dealer_value = self.calculate_hand_value(self.dealer_hand)
        dealer_display = self.hand_to_string(self.dealer_hand, not final and not self.game_over)
        dealer_value_text = f"({dealer_value})" if final or self.game_over else "(?)"
        embed.add_field(
            name=f"🎩 딜러 {dealer_value_text}",
            value=dealer_display,
            inline=False
        )

        # Split hands
        for i, hand in enumerate(self.split_hands):
            hand_value = self.calculate_hand_value(hand)
            hand_display = self.hand_to_string(hand)
            status = ""
            if hand_value > 21:
                status = " (버스트)"
            elif hand_value == 21 and len(hand) == 2:
                status = " (21)"

            current_indicator = " 👈" if i == self.current_hand and not final else ""
            embed.add_field(
                name=f"👤 핸드 {i + 1} ({hand_value}){status}{current_indicator}",
                value=hand_display,
                inline=False
            )

        # Bet info
        total_bet = self.bet * 2  # Split doubles the bet
        embed.add_field(name="💰 베팅 정보", value=f"총 베팅: {total_bet:,} 코인", inline=False)

        return embed

    async def end_game(self, interaction: discord.Interaction):
        """Handle game end and payouts"""
        self.game_over = True

        # Disable all buttons
        for item in self.children:
            item.disabled = True

        coins_cog = self.bot.get_cog('CoinsCog')
        if not coins_cog:
            return

        if self.is_split:
            await self.end_split_game(interaction)
            return

        player_value = self.calculate_hand_value(self.player_hand)
        dealer_value = self.calculate_hand_value(self.dealer_hand)
        total_payout = 0

        # Calculate main bet payout
        if self.player_blackjack and not self.dealer_blackjack:
            # Blackjack pays 3:2
            main_payout = int(self.bet * 2.5)
            total_payout += main_payout
            result = f"🎊 BLACKJACK! {main_payout} 코인 획득!"
        elif self.player_blackjack and self.dealer_blackjack:
            # Push on both blackjacks
            main_payout = self.bet
            total_payout += main_payout
            result = f"🤝 양쪽 블랙잭! {main_payout} 코인 반환"
        elif player_value > 21:
            result = f"💥 버스트! {self.bet * (2 if self.doubled_down else 1)} 코인 손실"
        elif dealer_value > 21 or player_value > dealer_value:
            main_payout = self.bet * (4 if self.doubled_down else 2)
            total_payout += main_payout
            result = f"🎉 승리! {main_payout} 코인 획득!"
        elif player_value == dealer_value:
            main_payout = self.bet * (2 if self.doubled_down else 1)
            total_payout += main_payout
            result = f"🤝 무승부! {main_payout} 코인 반환"
        else:
            result = f"😞 패배! {self.bet * (2 if self.doubled_down else 1)} 코인 손실"

        # Handle insurance bet
        if self.insurance_bet > 0:
            if self.dealer_blackjack:
                insurance_payout = self.insurance_bet * 3  # Insurance pays 2:1
                total_payout += insurance_payout
                result += f"\n💡 보험 적중! +{insurance_payout} 코인"
            else:
                result += f"\n❌ 보험 실패 -{self.insurance_bet} 코인"

        if total_payout > 0:
            await coins_cog.add_coins(self.user_id, total_payout, "blackjack_win", "Blackjack payout")

        embed = await self.create_embed(final=True)
        embed.add_field(name="결과", value=result, inline=False)

        new_balance = await coins_cog.get_user_coins(self.user_id)
        embed.add_field(name="현재 잔액", value=f"{new_balance:,} 코인", inline=False)

        await interaction.edit_original_response(embed=embed, view=self)

    async def end_split_game(self, interaction: discord.Interaction):
        """Handle split game end and payouts"""
        coins_cog = self.bot.get_cog('CoinsCog')
        dealer_value = self.calculate_hand_value(self.dealer_hand)
        total_payout = 0
        results = []

        for i, hand in enumerate(self.split_hands):
            hand_value = self.calculate_hand_value(hand)

            if hand_value > 21:
                results.append(f"핸드 {i + 1}: 버스트 (-{self.bet} 코인)")
            elif dealer_value > 21 or hand_value > dealer_value:
                payout = self.bet * 2
                total_payout += payout
                results.append(f"핸드 {i + 1}: 승리 (+{payout} 코인)")
            elif hand_value == dealer_value:
                payout = self.bet
                total_payout += payout
                results.append(f"핸드 {i + 1}: 무승부 (+{payout} 코인)")
            else:
                results.append(f"핸드 {i + 1}: 패배 (-{self.bet} 코인)")

        if total_payout > 0:
            await coins_cog.add_coins(self.user_id, total_payout, "blackjack_split_win", "Blackjack split payout")

        embed = await self.create_split_embed(final=True)
        embed.add_field(name="결과", value="\n".join(results), inline=False)

        new_balance = await coins_cog.get_user_coins(self.user_id)
        embed.add_field(name="현재 잔액", value=f"{new_balance:,} 코인", inline=False)

        await interaction.edit_original_response(embed=embed, view=self)

    @discord.ui.button(label="히트", style=discord.ButtonStyle.primary, emoji="➕")
    async def hit_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id or self.game_over:
            await interaction.response.send_message("❌ 권한이 없습니다!", ephemeral=True)
            return

        await interaction.response.defer()

        current_hand = self.split_hands[self.current_hand] if self.is_split else self.player_hand
        current_hand.append(self.draw_card())
        hand_value = self.calculate_hand_value(current_hand)

        if self.is_split:
            if hand_value > 21:  # Current hand busts
                if self.current_hand < len(self.split_hands) - 1:
                    # Move to next hand
                    self.current_hand += 1
                else:
                    # All hands played, dealer plays
                    while self.calculate_hand_value(self.dealer_hand) < 17:
                        self.dealer_hand.append(self.draw_card())
                    await self.end_game(interaction)
                    return

            # Disable split and double down after hitting
            for item in self.children:
                if hasattr(item, 'custom_id') and item.custom_id in ["split", "double_down"]:
                    item.disabled = True

        else:
            if hand_value > 21:
                await self.end_game(interaction)
                return

            # Disable double down and split after hitting
            for item in self.children:
                if hasattr(item, 'custom_id') and item.custom_id in ["double_down", "split"]:
                    item.disabled = True

        embed = await self.create_embed()
        await interaction.edit_original_response(embed=embed, view=self)

    @discord.ui.button(label="스탠드", style=discord.ButtonStyle.secondary, emoji="✋")
    async def stand_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id or self.game_over:
            await interaction.response.send_message("❌ 권한이 없습니다!", ephemeral=True)
            return

        await interaction.response.defer()

        if self.is_split:
            if self.current_hand < len(self.split_hands) - 1:
                # Move to next hand
                self.current_hand += 1
                embed = await self.create_embed()
                await interaction.edit_original_response(embed=embed, view=self)
                return

        # Dealer plays (hits on soft 17)
        while self.calculate_hand_value(self.dealer_hand) < 17:
            self.dealer_hand.append(self.draw_card())

        await self.end_game(interaction)

    @discord.ui.button(label="더블다운", style=discord.ButtonStyle.success, emoji="⬆️", custom_id="double_down")
    async def double_down_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id or self.game_over:
            await interaction.response.send_message("❌ 권한이 없습니다!", ephemeral=True)
            return

        if not self.can_double_down():
            await interaction.response.send_message("❌ 더블다운할 수 없습니다!", ephemeral=True)
            return

        await interaction.response.defer()

        coins_cog = self.bot.get_cog('CoinsCog')
        if not coins_cog:
            await interaction.followup.send("❌ 코인 시스템 오류!", ephemeral=True)
            return

        # Check if user has enough for double down
        user_coins = await coins_cog.get_user_coins(self.user_id)
        if user_coins < self.bet:
            await interaction.followup.send(f"❌ 더블다운 자금 부족! 필요: {self.bet}", ephemeral=True)
            return

        # Deduct additional bet
        if not await coins_cog.remove_coins(self.user_id, self.bet, "blackjack_double", "Blackjack double down"):
            await interaction.followup.send("❌ 더블다운 처리 실패!", ephemeral=True)
            return

        self.doubled_down = True

        # Hit exactly one card and stand
        current_hand = self.split_hands[self.current_hand] if self.is_split else self.player_hand
        current_hand.append(self.draw_card())
        hand_value = self.calculate_hand_value(current_hand)

        if self.is_split:
            if self.current_hand < len(self.split_hands) - 1:
                # Move to next hand
                self.current_hand += 1
                embed = await self.create_embed()
                await interaction.edit_original_response(embed=embed, view=self)
                return

        if not self.is_split and hand_value > 21:
            await self.end_game(interaction)
        else:
            # Dealer plays
            while self.calculate_hand_value(self.dealer_hand) < 17:
                self.dealer_hand.append(self.draw_card())
            await self.end_game(interaction)

    @discord.ui.button(label="스플릿", style=discord.ButtonStyle.danger, emoji="✂️", custom_id="split")
    async def split_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id or self.game_over:
            await interaction.response.send_message("❌ 권한이 없습니다!", ephemeral=True)
            return

        if not self.can_split():
            await interaction.response.send_message("❌ 스플릿할 수 없습니다!", ephemeral=True)
            return

        await interaction.response.defer()

        coins_cog = self.bot.get_cog('CoinsCog')
        if not coins_cog:
            return

        # Check if user has enough for split
        user_coins = await coins_cog.get_user_coins(self.user_id)
        if user_coins < self.bet:
            await interaction.followup.send(f"❌ 스플릿 자금 부족! 필요: {self.bet}", ephemeral=True)
            return

        # Deduct additional bet for split
        if not await coins_cog.remove_coins(self.user_id, self.bet, "blackjack_split", "Blackjack split"):
            await interaction.followup.send("❌ 스플릿 처리 실패!", ephemeral=True)
            return

        # Create split hands
        self.is_split = True
        self.split_hands = [[self.player_hand[0]], [self.player_hand[1]]]

        # Deal one card to each hand
        self.split_hands[0].append(self.draw_card())
        self.split_hands[1].append(self.draw_card())

        self.current_hand = 0

        # Disable split button after splitting
        button.disabled = True

        embed = await self.create_embed()
        await interaction.edit_original_response(embed=embed, view=self)

    @discord.ui.button(label="보험", style=discord.ButtonStyle.secondary, emoji="🛡️", custom_id="insurance")
    async def insurance_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id or self.game_over:
            await interaction.response.send_message("❌ 권한이 없습니다!", ephemeral=True)
            return

        if not self.can_insure or self.insurance_bet > 0:
            await interaction.response.send_message("❌ 보험을 걸 수 없습니다!", ephemeral=True)
            return

        await interaction.response.defer()

        coins_cog = self.bot.get_cog('CoinsCog')
        if not coins_cog:
            return

        insurance_amount = self.bet // 2
        user_coins = await coins_cog.get_user_coins(self.user_id)

        if user_coins < insurance_amount:
            await interaction.followup.send(f"❌ 보험료 부족! 필요: {insurance_amount}", ephemeral=True)
            return

        if await coins_cog.remove_coins(self.user_id, insurance_amount, "blackjack_insurance", "Blackjack insurance"):
            self.insurance_bet = insurance_amount
            button.disabled = True

            embed = await self.create_embed()
            embed.add_field(name="💡", value=f"보험료 {insurance_amount} 코인 지불완료", inline=False)
            await interaction.edit_original_response(embed=embed, view=self)


class BlackjackCog(commands.Cog):
    """Professional Blackjack with advanced features"""

    def __init__(self, bot):
        self.bot = bot
        self.logger = get_logger("블랙잭", bot=bot, discord_log_channel_id=config.LOG_CHANNEL_ID)
        self.logger.info("블랙잭 시스템이 초기화되었습니다.")

    @app_commands.command(name="블랙잭", description="전문적인 블랙잭 게임 (더블다운, 보험, 스플릿 포함)")
    @app_commands.describe(bet="베팅할 코인 수 (20-200)")
    async def blackjack(self, interaction: discord.Interaction, bet: int):
        if bet < 20 or bet > 200:
            await interaction.response.send_message("❌ 베팅은 20~200 코인 사이만 가능합니다.", ephemeral=True)
            return

        coins_cog = self.bot.get_cog('CoinsCog')
        if not coins_cog:
            await interaction.response.send_message("❌ 코인 시스템을 찾을 수 없습니다!", ephemeral=True)
            return

        user_coins = await coins_cog.get_user_coins(interaction.user.id)
        if user_coins < bet:
            await interaction.response.send_message(f"❌ 코인 부족! 필요: {bet:,}, 보유: {user_coins:,}", ephemeral=True)
            return

        # Deduct initial bet
        if not await coins_cog.remove_coins(interaction.user.id, bet, "blackjack_bet", "Blackjack initial bet"):
            await interaction.response.send_message("❌ 베팅 처리 실패!", ephemeral=True)
            return

        view = BlackjackView(self.bot, interaction.user.id, bet)

        # Disable buttons based on game state
        for item in view.children:
            if hasattr(item, 'custom_id'):
                if item.custom_id == "insurance" and not view.can_insure:
                    item.disabled = True
                elif item.custom_id == "split" and not view.can_split():
                    item.disabled = True
                elif view.game_over:  # Disable all if blackjack
                    item.disabled = True

        embed = await view.create_embed()

        # Handle immediate blackjack payout
        if view.player_blackjack:
            await view.end_game(interaction)
            return

        # Add strategy hints for non-blackjack hands
        if not view.game_over:
            player_val = view.calculate_hand_value(view.player_hand)
            dealer_up = view.dealer_hand[0]['rank']

            hints = []
            if view.can_double_down():
                if player_val == 11:
                    hints.append("💡 11에서 더블다운 추천")
                elif player_val == 10 and dealer_up not in ['10', 'J', 'Q', 'K', 'A']:
                    hints.append("💡 더블다운 고려해보세요")

            if view.can_split():
                hints.append("💡 스플릿 가능")

            if player_val <= 11:
                hints.append("🃏 버스트 불가능 - 히트 안전")
            elif player_val >= 17:
                hints.append("⚠️ 높은 수치 - 스탠드 고려")

            if hints:
                embed.add_field(name="🎯 전략 힌트", value="\n".join(hints), inline=False)

        await interaction.response.send_message(embed=embed, view=view)
        self.logger.info(f"{interaction.user}가 {bet} 코인으로 블랙잭 시작")


async def setup(bot):
    await bot.add_cog(BlackjackCog(bot))
# cogs/casino_base.py
import discord
from discord.ext import commands
from discord import app_commands
from datetime import datetime, timezone
from typing import Dict, List, Optional

from utils.logger import get_logger
from utils import config


class CasinoBaseCog(commands.Cog):
    """Base cog for casino functionality - provides shared utilities"""

    def __init__(self, bot):
        self.bot = bot
        self.logger = get_logger(
            "카지노 베이스",
            bot=self.bot,
            discord_log_channel_id=config.LOG_CHANNEL_ID
        )

        # Spam protection per game type
        self.game_cooldowns: Dict[int, Dict[str, datetime]] = {}  # user_id: {game_type: last_time}
        self.cooldown_seconds = 5

        # Channel restrictions - SET THESE MANUALLY
        self.ALLOWED_CHANNELS = {
            # Example:
            # 'slot_machine': [123456789, 987654321],
            # 'blackjack': [123456789],
            # 'roulette': [987654321],
            # 'dice': [123456789, 987654321],
        }

        self.logger.info("카지노 베이스 시스템이 초기화되었습니다.")

    def check_game_cooldown(self, user_id: int, game_type: str) -> bool:
        """Check if user is on cooldown for specific game"""
        now = datetime.now()

        if user_id not in self.game_cooldowns:
            self.game_cooldowns[user_id] = {}

        if game_type in self.game_cooldowns[user_id]:
            time_diff = (now - self.game_cooldowns[user_id][game_type]).total_seconds()
            if time_diff < self.cooldown_seconds:
                return False

        self.game_cooldowns[user_id][game_type] = now
        return True

    def check_channel_restriction(self, game_type: str, channel_id: int) -> bool:
        """Check if game is allowed in current channel"""
        if game_type in self.ALLOWED_CHANNELS:
            return channel_id in self.ALLOWED_CHANNELS[game_type]
        return True  # No restrictions set

    async def get_coins_cog(self):
        """Get the coins cog"""
        return self.bot.get_cog('CoinsCog')

    async def validate_game_start(self, interaction: discord.Interaction, game_type: str, bet: int, min_bet: int = 1,
                                  max_bet: int = 10000) -> tuple[bool, str]:
        """
        Validate if a game can be started
        Returns (can_start: bool, error_message: str)
        """
        # Check cooldown
        if not self.check_game_cooldown(interaction.user.id, game_type):
            return False, "⏳ 잠시 기다렸다가 다시 해주세요!"

        # Check channel restriction
        if not self.check_channel_restriction(game_type, interaction.channel.id):
            return False, f"❌ 이 채널에서는 {game_type}을(를) 플레이할 수 없습니다!"

        # Check bet limits
        if bet < min_bet or bet > max_bet:
            return False, f"❌ 베팅은 {min_bet}-{max_bet:,} 코인 사이만 가능합니다!"

        # Check coins cog
        coins_cog = await self.get_coins_cog()
        if not coins_cog:
            return False, "❌ 코인 시스템을 찾을 수 없습니다!"

        # Check user balance
        user_coins = await coins_cog.get_user_coins(interaction.user.id)
        if user_coins < bet:
            return False, f"❌ 코인이 부족합니다! 필요: {bet:,}, 보유: {user_coins:,}"

        return True, ""

    @app_commands.command(name="카지노통계", description="개인 카지노 게임 통계를 확인합니다.")
    async def casino_stats(self, interaction: discord.Interaction, user: discord.Member = None):
        await interaction.response.defer(ephemeral=True)

        target_user = user or interaction.user

        try:
            # Get transaction data
            query = """
                SELECT transaction_type, SUM(amount) as total, COUNT(*) as count
                FROM coin_transactions 
                WHERE user_id = $1 AND (transaction_type LIKE '%_win' OR transaction_type LIKE '%_bet' OR transaction_type LIKE '%_push')
                GROUP BY transaction_type
                ORDER BY transaction_type
            """
            stats = await self.bot.pool.fetch(query, target_user.id)

            if not stats:
                await interaction.followup.send(f"{target_user.display_name}님의 카지노 기록이 없습니다.", ephemeral=True)
                return

            # Process stats
            games_played = {}
            total_bet = 0
            total_won = 0

            for record in stats:
                trans_type = record['transaction_type']
                amount = record['total']
                count = record['count']

                # Extract game name
                game_name = trans_type.replace('_bet', '').replace('_win', '').replace('_push', '')

                if game_name not in games_played:
                    games_played[game_name] = {'bets': 0, 'wins': 0, 'games': 0, 'net': 0}

                if '_bet' in trans_type:
                    games_played[game_name]['bets'] += abs(amount)  # Bets are negative
                    games_played[game_name]['games'] += count
                    total_bet += abs(amount)
                elif '_win' in trans_type:
                    games_played[game_name]['wins'] += amount
                    total_won += amount
                elif '_push' in trans_type:
                    games_played[game_name]['wins'] += amount  # Pushes are returns

            # Create embed
            embed = discord.Embed(
                title=f"🎰 {target_user.display_name}님의 카지노 통계",
                color=discord.Color.gold(),
                timestamp=datetime.now(timezone.utc)
            )

            # Overall stats
            net_profit = total_won - total_bet
            embed.add_field(
                name="📊 전체 통계",
                value=f"총 베팅: {total_bet:,} 코인\n총 당첨: {total_won:,} 코인\n순 손익: {net_profit:+,} 코인",
                inline=False
            )

            # Individual game stats
            for game, data in games_played.items():
                if data['games'] > 0:
                    win_rate = (data['wins'] / data['bets'] * 100) if data['bets'] > 0 else 0
                    game_net = data['wins'] - data['bets']

                    game_names = {
                        'slot_machine': '🎰 슬롯머신',
                        'blackjack': '🃏 블랙잭',
                        'roulette': '🎡 룰렛',
                        'dice_game': '🎲 주사위',
                        'coinflip': '🪙 동전던지기',
                        'hilow': '🔢 하이로우',
                        'lottery': '🎫 복권',
                        'crash': '🚀 크래시',
                        'plinko': '📍 플링코',
                        'wheel': '🎡 행운의 바퀴',
                        'mines': '💣 지뢰찾기',
                        'keno': '🔢 케노',
                        'bingo': '🎱 빙고',
                        'scratch': '🎫 스크래치',
                        'war': '⚔️ 카드워',
                        'holdem': '🃏 홀덤'
                    }

                    game_display = game_names.get(game, game.title())

                    embed.add_field(
                        name=game_display,
                        value=f"게임 수: {data['games']}\n베팅: {data['bets']:,}\n당첨: {data['wins']:,}\n손익: {game_net:+,}",
                        inline=True
                    )

            embed.set_thumbnail(url=target_user.display_avatar.url)
            embed.set_footer(text="모든 거래 내역을 기반으로 계산됨")

            await interaction.followup.send(embed=embed, ephemeral=True)

        except Exception as e:
            await interaction.followup.send(f"❌ 통계를 불러오는 중 오류가 발생했습니다: {e}", ephemeral=True)
            self.logger.error(f"Error in casino_stats: {e}")

    @app_commands.command(name="카지노도움", description="카지노 게임 설명 및 도움말을 확인합니다.")
    async def casino_help(self, interaction: discord.Interaction):
        embed = discord.Embed(
            title="🎰 카지노 게임 가이드",
            description="사용 가능한 모든 카지노 게임과 규칙을 안내합니다.",
            color=discord.Color.blue()
        )

        embed.add_field(
            name="🎰 클래식 게임",
            value="• `/슬롯` - 슬롯머신 (10-1000 코인)\n• `/블랙잭` - 21 만들기 (20-2000 코인)\n• `/룰렛` - 유럽식 룰렛 (다양한 베팅)\n• `/바카라` - 뱅커 vs 플레이어 (50-5000 코인)",
            inline=False
        )

        embed.add_field(
            name="🎲 주사위 & 확률 게임",
            value="• `/주사위` - 합 맞히기 (5-500 코인)\n• `/동전던지기` - 앞뒤 맞히기 (5-1000 코인)\n• `/하이로우` - 7 기준 높낮이 (10-800 코인)\n• `/케노` - 숫자 선택 게임 (20-1000 코인)",
            inline=False
        )

        embed.add_field(
            name="🎮 특수 게임",
            value="• `/크래시` - 배수 예측 게임 (10-2000 코인)\n• `/플링코` - 공 떨어뜨리기 (5-500 코인)\n• `/지뢰찾기` - 지뢰 피하기 (10-1000 코인)\n• `/행운의바퀴` - 스핀 게임 (25-1500 코인)",
            inline=False
        )

        embed.add_field(
            name="🃏 카드 게임",
            value="• `/카드워` - 높은 카드 승부 (15-800 코인)\n• `/홀덤` - 텍사스 홀덤 (100-5000 코인)\n• `/스크래치` - 스크래치 복권 (10-200 코인)",
            inline=False
        )

        embed.add_field(
            name="🎫 추첨 게임",
            value="• `/복권` - 번호 맞히기 (50-1000 코인)\n• `/빙고` - 빙고 게임 (30-600 코인)",
            inline=False
        )

        embed.add_field(
            name="📊 기타 명령어",
            value="• `/카지노통계` - 개인 게임 통계\n• `/코인` - 현재 코인 확인\n• `/코인주기` - 코인 전송",
            inline=False
        )

        embed.add_field(
            name="⚠️ 주의사항",
            value="• 도박은 적당히!\n• 모든 게임에는 쿨다운이 있습니다 (5초)\n• 일부 게임은 특정 채널에서만 가능할 수 있습니다\n• 모든 거래는 로그에 기록됩니다",
            inline=False
        )

        embed.set_footer(text="책임감 있는 게임 플레이를 권장합니다")

        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot):
    await bot.add_cog(CasinoBaseCog(bot))
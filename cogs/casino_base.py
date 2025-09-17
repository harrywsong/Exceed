# cogs/casino_base.py - Updated for multi-server support
import discord
from discord.ext import commands
from discord import app_commands
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

from utils.logger import get_logger
from utils.config import (
    get_channel_id,
    is_feature_enabled,
    is_server_configured,
    get_server_setting
)


class CasinoBaseCog(commands.Cog):
    """Base cog for casino functionality - provides shared utilities"""

    def __init__(self, bot):
        self.bot = bot
        self.logger = get_logger("카지노 베이스", bot=self.bot)

        # Spam protection per game type
        self.game_cooldowns: Dict[int, Dict[str, datetime]] = {}  # user_id: {game_type: last_time}
        self.cooldown_seconds = 5

        # Mapping of game_type to specific channel key
        self.CHANNEL_MAP = {
            'slot_machine': 'slots_channel',
            'blackjack': 'blackjack_channel',
            'hilow': 'hilow_channel',
            'dice_game': 'dice_channel',
            'roulette': 'roulette_channel',
            'lottery': 'lottery_channel',
            'coinflip': 'coinflip_channel',
            'minesweeper': 'minesweeper_channel',
            'bingo': 'bingo_channel',
            'crash': 'crash_channel',
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

    def check_channel_restriction(self, guild_id: int, game_type: str, channel_id: int) -> Tuple[bool, str]:
        """Check if game is allowed in current channel for this server"""
        channel_key = self.CHANNEL_MAP.get(game_type)
        if not channel_key:
            return True, ""

        game_channel_id = get_channel_id(guild_id, channel_key)

        if game_channel_id and game_channel_id != channel_id:
            guild = self.bot.get_guild(guild_id)
            if guild:
                channel = guild.get_channel(game_channel_id)
                mention = channel.mention if channel else f"<#{game_channel_id}>"
            else:
                mention = f"<#{game_channel_id}>"
            return False, f"❌ 이 게임은 {mention} 채널에서만 플레이할 수 있습니다!"

        return True, ""

    async def get_coins_cog(self):
        """Get the coins cog"""
        return self.bot.get_cog('CoinsCog')

    async def validate_game_start(self, interaction: discord.Interaction, game_type: str, bet: int,
                                  min_bet: int = 1, max_bet: int = 10000) -> tuple[bool, str]:
        """
        Validate if a game can be started for this specific server
        Returns (can_start: bool, error_message: str)
        """
        guild_id = interaction.guild.id if interaction.guild else None

        if not guild_id:
            return False, "❌ 이 명령어는 서버에서만 사용할 수 있습니다!"

        # Check if server is configured
        if not is_server_configured(guild_id):
            return False, "❌ 이 서버는 아직 설정되지 않았습니다! 관리자에게 `/봇셋업` 명령어 실행을 요청하세요."

        # Check if casino games are enabled for this server
        if not is_feature_enabled(guild_id, 'casino_games'):
            return False, "❌ 이 서버에서는 카지노 게임이 비활성화되어 있습니다!"

        # Check cooldown
        if not self.check_game_cooldown(interaction.user.id, game_type):
            return False, "⏳ 잠시 기다렸다가 다시 해주세요!"

        # Check channel restriction
        allowed, channel_msg = self.check_channel_restriction(guild_id, game_type, interaction.channel.id)
        if not allowed:
            return False, channel_msg

        # Get server-specific bet limits
        server_min_bet = get_server_setting(guild_id, 'min_bet', min_bet)
        server_max_bet = get_server_setting(guild_id, 'max_bet', max_bet)

        # Check bet limits
        if bet < server_min_bet or bet > server_max_bet:
            return False, f"❌ 베팅은 {server_min_bet}-{server_max_bet:,} 코인 사이만 가능합니다!"

        # Check coins cog
        coins_cog = await self.get_coins_cog()
        if not coins_cog:
            return False, "❌ 코인 시스템을 찾을 수 없습니다!"

        # Check user balance
        user_coins = await coins_cog.get_user_coins(interaction.user.id, interaction.guild.id)
        if user_coins < bet:
            return False, f"❌ 코인이 부족합니다! 필요: {bet:,}, 보유: {user_coins:,}"

        return True, ""

    @app_commands.command(name="카지노통계", description="개인 카지노 게임 통계를 확인합니다.")
    async def casino_stats(self, interaction: discord.Interaction, user: discord.Member = None):
        # Check if casino games are enabled
        if not interaction.guild or not is_feature_enabled(interaction.guild.id, 'casino_games'):
            await interaction.response.send_message("❌ 이 서버에서는 카지노 통계를 볼 수 없습니다!", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        target_user = user or interaction.user

        try:
            # Check if bot has database access
            if not hasattr(self.bot, 'pool') or not self.bot.pool:
                await interaction.followup.send("❌ 데이터베이스 연결을 찾을 수 없습니다!", ephemeral=True)
                return

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
                        'slot_machine': '🎰 슬롯',
                        'blackjack': '🃏 블랙잭',
                        'hilow': '🔢 하이로우',
                        'dice_game': '🎲 주사위',
                        'roulette': '🎡 룰렛',
                        'lottery': '🎫 복권',
                        'coinflip': '🪙 동전던지기',
                        'minesweeper': '💣 지뢰찾기',
                        'bingo': '🎱 빙고',
                        'crash': '🚀 크래시',
                    }
                    game_display = game_names.get(game, game.title())

                    embed.add_field(
                        name=game_display,
                        value=f"게임 수: {data['games']}\n베팅: {data['bets']:,}\n당첨: {data['wins']:,}\n손익: {game_net:+,}",
                        inline=True
                    )

            embed.set_thumbnail(url=target_user.display_avatar.url)
            embed.set_footer(text=f"Server: {interaction.guild.name} | 모든 거래 내역 기준")

            await interaction.followup.send(embed=embed, ephemeral=True)

        except Exception as e:
            await interaction.followup.send(f"❌ 통계를 불러오는 중 오류가 발생했습니다: {e}", ephemeral=True)
            self.logger.error(f"Error in casino_stats for guild {interaction.guild.id}: {e}")

    @app_commands.command(name="카지노도움", description="카지노 게임 설명 및 도움말을 확인합니다.")
    async def casino_help(self, interaction: discord.Interaction):
        # Check if casino games are enabled
        if not interaction.guild or not is_feature_enabled(interaction.guild.id, 'casino_games'):
            await interaction.response.send_message("❌ 이 서버에서는 카지노 게임이 비활성화되어 있습니다!", ephemeral=True)
            return

        embed = discord.Embed(
            title="🎰 카지노 게임 가이드",
            description="사용 가능한 모든 카지노 게임과 규칙을 안내합니다.",
            color=discord.Color.blue()
        )

        games_list = [
            ("🎰 슬롯", "`/슬롯` - 슬롯머신"),
            ("🃏 블랙잭", "`/블랙잭` - 21 만들기"),
            ("🔢 하이로우", "`/하이로우` - 7 기준 높낮이"),
            ("🎲 주사위", "`/주사위` - 합 맞히기"),
            ("🎡 룰렛", "`/룰렛` - 유럽식 룰렛"),
            ("🎫 복권", "`/복권` - 번호 맞히기"),
            ("🪙 동전던지기", "`/동전던지기` - 앞뒤 맞히기"),
            ("💣 지뢰찾기", "`/지뢰찾기` - 지뢰 피하기"),
            ("🎱 빙고", "`/빙고` - 빙고 게임"),
            ("🚀 크래시", "`/크래시` - 배수 예측 게임"),
        ]

        for name, value in games_list:
            embed.add_field(name=name, value=value, inline=True)

        embed.add_field(
            name="📊 기타 명령어",
            value="• `/카지노통계` - 개인 게임 통계\n• `/코인` - 현재 코인 확인\n• `/코인주기` - 코인 전송",
            inline=False
        )

        embed.add_field(
            name="⚠️ 주의사항",
            value="• 도박은 적당히!\n• 모든 게임에는 쿨다운이 있습니다 (5초)\n• 각 게임은 설정된 전용 채널에서만 가능\n• 모든 거래는 로그에 기록됩니다",
            inline=False
        )

        embed.set_footer(text=f"Server: {interaction.guild.name} | 책임감 있는 게임 플레이를 권장합니다")

        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot):
    await bot.add_cog(CasinoBaseCog(bot))
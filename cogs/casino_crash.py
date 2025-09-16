# cogs/casino_crash.py - Updated for multi-server support
import discord
from discord.ext import commands
from discord import app_commands
import asyncio
import random
import math
import os
import io
import matplotlib
import matplotlib.pyplot as plt
from matplotlib import font_manager
from typing import Dict

from utils.logger import get_logger
from utils.config import (
    is_feature_enabled,
    get_channel_id,
    get_server_setting
)

# Font setup for Korean text (same as crash_game.py)
here = os.path.dirname(os.path.dirname(__file__))
font_path = os.path.join(here, "assets", "fonts", "NotoSansKR-Bold.ttf")

# Load font file into Matplotlib if it exists
if os.path.exists(font_path):
    font_manager.fontManager.addfont(font_path)
    font_name = font_manager.FontProperties(fname=font_path).get_name()
    plt.rcParams['font.family'] = font_name
    plt.rcParams['axes.unicode_minus'] = False
    font_prop = font_manager.FontProperties(fname=font_path)
    matplotlib.rc('font', family=font_prop.get_name())
    matplotlib.rcParams['axes.unicode_minus'] = False
else:
    # Fallback to default font if Korean font not available
    font_prop = None


class CrashGame:
    """Shared crash game instance for multiple players"""

    def __init__(self, bot, crash_point: float, guild_id: int):
        self.bot = bot
        self.guild_id = guild_id
        self.crash_point = crash_point
        self.players: Dict[int, dict] = {}  # user_id: {bet: int, cashed_out: bool, cash_out_multiplier: float}
        self.current_multiplier = 1.0
        self.game_started = False
        self.game_over = False
        self.start_time = None
        self.history: list[float] = [1.0]  # Track multiplier history for chart

    def add_player(self, user_id: int, bet: int):
        """Add a player to the game"""
        self.players[user_id] = {
            'bet': bet,
            'cashed_out': False,
            'cash_out_multiplier': 0.0
        }

    def cash_out_player(self, user_id: int) -> bool:
        """Cash out a player"""
        if user_id in self.players and not self.players[user_id]['cashed_out'] and not self.game_over:
            self.players[user_id]['cashed_out'] = True
            self.players[user_id]['cash_out_multiplier'] = self.current_multiplier
            return True
        return False

    def get_active_players_count(self) -> int:
        """Get count of players who haven't cashed out"""
        return sum(1 for p in self.players.values() if not p['cashed_out'])

    def update_multiplier(self, new_multiplier: float):
        """Update multiplier and add to history"""
        self.current_multiplier = new_multiplier
        self.history.append(new_multiplier)


class JoinBetModal(discord.ui.Modal, title="크래시 게임 참가"):
    """Modal for a player to enter their bet amount."""
    bet_amount = discord.ui.TextInput(
        label="베팅 금액",
        placeholder="베팅할 금액을 입력하세요 (예: 100)",
        min_length=1,
        max_length=10,
    )

    def __init__(self, cog: 'CrashCog', game: CrashGame, view: 'CrashView'):
        super().__init__()
        self.cog = cog
        self.game = game
        self.view = view

    async def on_submit(self, interaction: discord.Interaction):
        try:
            bet = int(self.bet_amount.value)
        except ValueError:
            await interaction.response.send_message("⚠ 유효한 숫자를 입력해주세요.", ephemeral=True)
            return

        if self.game and interaction.user.id in self.game.players:
            await interaction.response.send_message("⚠ 이미 현재 게임에 참가 중입니다!", ephemeral=True)
            return

        can_start, error_msg = await self.cog.validate_game(interaction, bet)
        if not can_start:
            await interaction.response.send_message(error_msg, ephemeral=True)
            return

        coins_cog = self.cog.bot.get_cog('CoinsCog')
        if not await coins_cog.remove_coins(interaction.user.id, bet, "crash_bet", "Crash game bet"):
            await interaction.response.send_message("베팅 처리 실패!", ephemeral=True)
            return

        self.game.add_player(interaction.user.id, bet)

        embed = await self.view.create_embed()
        chart_file = await self.view.create_chart()
        await self.cog.game_message.edit(embed=embed, attachments=[chart_file] if chart_file else [])

        await interaction.response.send_message(f"✅ 게임에 참가했습니다! ({bet:,} 코인)", ephemeral=True)
        self.cog.logger.info(f"{interaction.user}가 {bet} 코인으로 크래시 게임 참가 (Guild: {interaction.guild.id})")


class CrashView(discord.ui.View):
    """Interactive crash game view for multiple players - Multi-server aware"""

    def __init__(self, cog: 'CrashCog', game: CrashGame):
        super().__init__(timeout=None)
        self.cog = cog
        self.bot = cog.bot
        self.game = game
        self.update_button_states()

    def update_button_states(self):
        """Enables/disables buttons based on the current game state."""
        # Waiting for players
        if not self.game.game_started and not self.game.game_over:
            self.join_button.disabled = False
            self.leave_button.disabled = False
            self.start_button.disabled = False
            self.cash_out_button.disabled = True
        # Game is running
        elif self.game.game_started and not self.game.game_over:
            self.join_button.disabled = True
            self.leave_button.disabled = True
            self.start_button.disabled = True
            self.cash_out_button.disabled = False
        # Game is over
        else:
            self.join_button.disabled = True
            self.leave_button.disabled = True
            self.start_button.disabled = True
            self.cash_out_button.disabled = True

    def draw_chart(self) -> io.BytesIO:
        """Create a chart showing the multiplier progression"""
        plt.figure(figsize=(10, 6))

        # Plot the multiplier history
        time_points = list(range(len(self.game.history)))
        plt.plot(time_points, self.game.history, 'b-', linewidth=2, label='배수')

        # Add crash point line if game is over
        if self.game.game_over:
            plt.axhline(y=self.game.crash_point, color='r', linestyle='--',
                        linewidth=2, alpha=0.7, label=f'크래시 지점: {self.game.crash_point:.2f}x')

        # Mark cashout points
        for user_id, player_data in self.game.players.items():
            if player_data['cashed_out']:
                cashout_time = len([h for h in self.game.history if h <= player_data['cash_out_multiplier']])
                plt.scatter(cashout_time, player_data['cash_out_multiplier'],
                            color='green', s=100, zorder=5, alpha=0.8)

        plt.xlabel('시간 (초)', fontproperties=font_prop if font_prop else None)
        plt.ylabel('배수', fontproperties=font_prop if font_prop else None)
        plt.title(f'크래시 게임 진행 상황 - 현재: {self.game.current_multiplier:.2f}x',
                  fontproperties=font_prop if font_prop else None)
        plt.grid(True, alpha=0.3)
        plt.legend(prop=font_prop if font_prop else None)

        # Set y-axis to show a bit above current multiplier
        max_y = max(self.game.current_multiplier * 1.2, 2.0)
        plt.ylim(1.0, max_y)

        buf = io.BytesIO()
        plt.savefig(buf, format='png', dpi=100, bbox_inches='tight')
        plt.close()
        buf.seek(0)
        return buf

    async def create_chart(self) -> discord.File:
        """Create chart file for Discord"""
        try:
            buf = self.draw_chart()
            return discord.File(buf, filename="crash_chart.png")
        except Exception as e:
            self.cog.logger.error(f"차트 생성 실패: {e}")
            return None

    async def create_embed(self, final: bool = False) -> discord.Embed:
        """Create game state embed"""
        if self.game.game_over and final:
            title = f"💥 로켓이 {self.game.crash_point:.2f}x에서 추락했습니다!"
            color = discord.Color.red()
        elif self.game.game_started:
            title = f"🚀 크래시 진행 중... {self.game.current_multiplier:.2f}x"
            color = discord.Color.orange()
        else:
            title = "🚀 크래시 게임 대기 중... (30초 후 시작)"
            color = discord.Color.blue()

        embed = discord.Embed(title=title, color=color)

        if self.game.game_started:
            embed.add_field(
                name="📊 현재 상태",
                value=f"현재 배수: **{self.game.current_multiplier:.2f}x**\n활성 플레이어: {self.game.get_active_players_count()}명",
                inline=False
            )

        if self.game.players:
            player_info = []
            for user_id, player_data in list(self.game.players.items())[:10]:
                try:
                    user = self.bot.get_user(user_id) or await self.bot.fetch_user(user_id)
                    username = user.display_name if user else f"User {user_id}"

                    if player_data['cashed_out']:
                        status = f"✅ {player_data['cash_out_multiplier']:.2f}x"
                        payout = int(player_data['bet'] * player_data['cash_out_multiplier'])
                        player_info.append(f"{username}: {status} (+{payout:,})")
                    elif self.game.game_over:
                        status = "💥 추락"
                        player_info.append(f"{username}: {status} (-{player_data['bet']:,})")
                    else:
                        player_info.append(f"{username}: 🎲 대기중 ({player_data['bet']:,})")
                except:
                    continue

            if player_info:
                embed.add_field(
                    name=f"👥 플레이어 현황 ({len(self.game.players)}명)",
                    value="\n".join(player_info),
                    inline=False
                )

        if not self.game.game_started:
            embed.add_field(
                name="📋 게임 규칙",
                value="• '게임 참가' 버튼을 눌러 베팅하세요.\n• 로켓이 추락하기 전에 캐시아웃하여 승리하세요!\n• '지금 시작'을 누르거나 30초를 기다리면 게임이 시작됩니다.",
                inline=False
            )

        # Add server info
        guild = self.bot.get_guild(self.game.guild_id)
        if guild:
            embed.set_footer(
                text=f"Server: {guild.name} | 📊 실시간 차트가 첨부되어 있습니다" if self.game.game_started or self.game.game_over else f"Server: {guild.name}")

        return embed

    @discord.ui.button(label="게임 참가", style=discord.ButtonStyle.green, emoji="🎲", custom_id="join_game")
    async def join_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.game.game_started:
            await interaction.response.send_message("⚠ 이미 게임이 시작되었습니다.", ephemeral=True)
            return
        modal = JoinBetModal(self.cog, self.game, self)
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="게임 나가기", style=discord.ButtonStyle.red, emoji="❌", custom_id="leave_game")
    async def leave_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.game.game_started:
            await interaction.response.send_message("⚠ 이미 게임이 시작되었습니다.", ephemeral=True)
            return

        player_data = self.game.players.get(interaction.user.id)
        if not player_data:
            await interaction.response.send_message("⚠ 이 게임에 참가하지 않았습니다!", ephemeral=True)
            return

        bet_amount = player_data['bet']
        del self.game.players[interaction.user.id]

        coins_cog = self.bot.get_cog('CoinsCog')
        if coins_cog:
            await coins_cog.add_coins(interaction.user.id, bet_amount, "crash_leave", "Crash game leave refund")

        embed = await self.create_embed()
        chart_file = await self.create_chart()
        await interaction.message.edit(embed=embed, attachments=[chart_file] if chart_file else [])
        await interaction.response.send_message(f"✅ 게임에서 나갔습니다. {bet_amount:,} 코인이 환불되었습니다.", ephemeral=True)

    @discord.ui.button(label="지금 시작", style=discord.ButtonStyle.blurple, emoji="🚀", custom_id="start_game_now")
    async def start_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.game.game_started:
            await interaction.response.send_message("⚠ 이미 게임이 시작되었습니다.", ephemeral=True)
            return
        if not self.game.players:
            await interaction.response.send_message("⚠ 참가한 플레이어가 없어 게임을 시작할 수 없습니다.", ephemeral=True)
            return

        self.cog.start_event.set()
        await interaction.response.send_message("🚀 게임을 곧 시작합니다!", ephemeral=True)

    @discord.ui.button(label="캐시아웃", style=discord.ButtonStyle.success, emoji="💸", custom_id="cash_out")
    async def cash_out_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self.game.game_started:
            await interaction.response.send_message("⚠ 아직 게임이 시작되지 않았습니다!", ephemeral=True)
            return
        if interaction.user.id not in self.game.players:
            await interaction.response.send_message("⚠ 이 게임에 참가하지 않았습니다!", ephemeral=True)
            return
        if self.game.players[interaction.user.id]['cashed_out']:
            await interaction.response.send_message("⚠ 이미 캐시아웃했습니다!", ephemeral=True)
            return

        await interaction.response.defer()
        if self.game.cash_out_player(interaction.user.id):
            player_data = self.game.players[interaction.user.id]
            payout = int(player_data['bet'] * player_data['cash_out_multiplier'])

            coins_cog = self.bot.get_cog('CoinsCog')
            if coins_cog:
                await coins_cog.add_coins(interaction.user.id, payout, "crash_win",
                                          f"Crash cashout at {player_data['cash_out_multiplier']:.2f}x")

            await interaction.followup.send(
                f"✅ {interaction.user.mention}님이 **{player_data['cash_out_multiplier']:.2f}x**에서 캐시아웃! {payout:,} 코인 획득!",
                ephemeral=False
            )
        else:
            await interaction.followup.send("⚠ 캐시아웃에 실패했습니다. 게임이 이미 종료되었을 수 있습니다.", ephemeral=True)


class CrashCog(commands.Cog):
    """Crash multiplier prediction game with multiple players - Multi-server aware"""

    def __init__(self, bot):
        self.bot = bot
        self.logger = get_logger("크래시", bot=bot)
        # Per-server game tracking
        self.server_games: Dict[int, CrashGame] = {}  # guild_id -> current_game
        self.server_messages: Dict[int, discord.Message] = {}  # guild_id -> game_message
        self.server_views: Dict[int, CrashView] = {}  # guild_id -> game_view
        self.start_events: Dict[int, asyncio.Event] = {}  # guild_id -> start_event
        self.logger.info("크래시 게임 시스템이 초기화되었습니다.")

    @property
    def current_game(self):
        """Backward compatibility property"""
        return None

    @property
    def game_message(self):
        """Backward compatibility property"""
        return None

    @property
    def game_view(self):
        """Backward compatibility property"""
        return None

    @property
    def start_event(self):
        """Backward compatibility property"""
        return None

    async def validate_game(self, interaction: discord.Interaction, bet: int):
        """Validate game using casino base"""
        casino_base = self.bot.get_cog('CasinoBaseCog')
        if not casino_base:
            return False, "카지노 시스템을 찾을 수 없습니다!"

        # Get server-specific limits
        min_bet = get_server_setting(interaction.guild.id, 'crash_min_bet', 10)
        max_bet = get_server_setting(interaction.guild.id, 'crash_max_bet', 2000)

        return await casino_base.validate_game_start(interaction, "crash", bet, min_bet, max_bet)

    def generate_crash_point(self) -> float:
        """Generate crash point with custom odds distribution"""
        rand = random.random()

        if rand <= 0.50:  # 50% chance for 1.1x - 2.0x (safe cashouts)
            return round(random.uniform(1.1, 2.0), 2)
        elif rand <= 0.85:  # 35% chance for 2.0x - 4.0x (moderate risk)
            return round(random.uniform(2.0, 4.0), 2)
        elif rand <= 0.95:  # 10% chance for 4.0x - 10.0x (good multipliers)
            return round(random.uniform(4.0, 10.0), 2)
        elif rand <= 0.99:  # 4% chance for 10.0x - 50.0x (high risk/reward)
            return round(random.uniform(10.0, 50.0), 2)
        else:  # 1% chance for 100.0x (jackpot)
            return 100.0

    async def announce_crash_point(self, guild_id: int, crash_point: float):
        """Announce crash point to the announcement channel for this server"""
        try:
            # Try to get server-specific announcement channel
            announcement_channel_id = get_channel_id(guild_id, 'announcement_channel')
            if not announcement_channel_id:
                return  # No announcement channel configured for this server

            channel = self.bot.get_channel(announcement_channel_id)
            if channel:
                embed = discord.Embed(
                    title="🚀 새로운 크래시 라운드",
                    description=f"다음 크래시 지점이 생성되었습니다: **{crash_point:.2f}x**\n\n게임 채널에서 참여하세요!",
                    color=discord.Color.green()
                )
                guild = self.bot.get_guild(guild_id)
                if guild:
                    embed.set_footer(text=f"Server: {guild.name}")
                await channel.send(embed=embed)
        except Exception as e:
            self.logger.error(f"크래시 지점 공지 실패 for guild {guild_id}: {e}")

    async def game_lifecycle_task(self, guild_id: int):
        """Manages the waiting period, game execution, and cleanup for a specific server."""
        try:
            current_game = self.server_games.get(guild_id)
            if not current_game:
                return

            self.logger.info(f"크래시 게임 대기 시작 for guild {guild_id}. {current_game.crash_point:.2f}x에서 추락 예정.")

            # Update with initial chart
            game_view = self.server_views.get(guild_id)
            game_message = self.server_messages.get(guild_id)
            start_event = self.start_events.get(guild_id)

            if not (game_view and game_message and start_event):
                return

            game_view.update_button_states()
            embed = await game_view.create_embed()
            chart_file = await game_view.create_chart()
            await game_message.edit(embed=embed, view=game_view,
                                    attachments=[chart_file] if chart_file else [])

            try:
                await asyncio.wait_for(start_event.wait(), timeout=30.0)
                self.logger.info(f"'지금 시작' 버튼으로 게임 시작 for guild {guild_id}.")
            except asyncio.TimeoutError:
                self.logger.info(f"30초 타임아웃으로 게임 시작 for guild {guild_id}.")

        except Exception as e:
            self.logger.error(f"Game lifecycle error for guild {guild_id}: {e}")
            return

        if not current_game.players:
            self.logger.warning(f"플레이어가 없어 게임 취소 for guild {guild_id}.")
            if game_message:
                try:
                    await game_message.edit(content="💥 참가자가 없어 게임이 취소되었습니다.", embed=None, view=None,
                                            attachments=[])
                except discord.NotFound:
                    pass
            # Clean up server game data
            self.cleanup_server_game(guild_id)
            return

        current_game.game_started = True
        game_view.update_button_states()

        try:
            embed = await game_view.create_embed()
            chart_file = await game_view.create_chart()
            await game_message.edit(embed=embed, view=game_view,
                                    attachments=[chart_file] if chart_file else [])
        except (discord.NotFound, discord.HTTPException):
            self.logger.error(f"게임 시작 메시지 업데이트 실패 for guild {guild_id}.")

        await self.run_crash_game(guild_id)

    async def run_crash_game(self, guild_id: int):
        """Run the crash game loop, increasing the multiplier for a specific server."""
        current_game = self.server_games.get(guild_id)
        game_message = self.server_messages.get(guild_id)
        game_view = self.server_views.get(guild_id)

        if not all([current_game, game_message, game_view]):
            return

        while (current_game.current_multiplier < current_game.crash_point and
               current_game.get_active_players_count() > 0):

            await asyncio.sleep(0.75)

            # Increase multiplier based on current value
            increment = 0.01 + (current_game.current_multiplier / 20)
            new_multiplier = current_game.current_multiplier + increment
            new_multiplier = round(new_multiplier, 2)
            current_game.update_multiplier(new_multiplier)

            try:
                embed = await game_view.create_embed()
                chart_file = await game_view.create_chart()
                await game_message.edit(embed=embed, attachments=[chart_file] if chart_file else [])
            except (discord.NotFound, discord.HTTPException):
                pass

        await self.end_crash_game(guild_id)

    async def end_crash_game(self, guild_id: int):
        """End the crash game, handle final states, and clean up for a specific server."""
        current_game = self.server_games.get(guild_id)
        game_message = self.server_messages.get(guild_id)
        game_view = self.server_views.get(guild_id)

        if not current_game:
            return

        current_game.game_over = True

        # Final update to the game message
        if game_message and game_view:
            try:
                game_view.update_button_states()
                embed = await game_view.create_embed(final=True)
                chart_file = await game_view.create_chart()
                await game_message.edit(embed=embed, view=game_view,
                                        attachments=[chart_file] if chart_file else [])
            except (discord.NotFound, discord.HTTPException):
                pass

        self.logger.info(f"크래시 게임 종료 for guild {guild_id}. 추락 지점: {current_game.crash_point:.2f}x")
        self.cleanup_server_game(guild_id)

    def cleanup_server_game(self, guild_id: int):
        """Clean up server-specific game data"""
        if guild_id in self.server_games:
            del self.server_games[guild_id]
        if guild_id in self.server_messages:
            del self.server_messages[guild_id]
        if guild_id in self.server_views:
            del self.server_views[guild_id]
        if guild_id in self.start_events:
            del self.start_events[guild_id]

    @app_commands.command(name="크래시", description="로켓이 추락하기 전에 캐시아웃하는 다중 플레이어 게임")
    @app_commands.describe(bet="베팅 금액")
    async def crash(self, interaction: discord.Interaction, bet: int):
        # Check if casino games are enabled for this server
        if not interaction.guild or not is_feature_enabled(interaction.guild.id, 'casino_games'):
            await interaction.response.send_message("❌ 이 서버에서는 카지노 게임이 비활성화되어 있습니다!", ephemeral=True)
            return

        guild_id = interaction.guild.id

        # Check if there's already an active game for this server
        if guild_id in self.server_games:
            await interaction.response.send_message("⚠ 이 서버에서 다른 크래시 게임이 이미 진행 중이거나 대기 중입니다.", ephemeral=True)
            return

        can_start, error_msg = await self.validate_game(interaction, bet)
        if not can_start:
            await interaction.response.send_message(error_msg, ephemeral=True)
            return

        coins_cog = self.bot.get_cog('CoinsCog')
        if not await coins_cog.remove_coins(interaction.user.id, bet, "crash_bet", "Crash game bet"):
            await interaction.response.send_message("베팅 처리 실패!", ephemeral=True)
            return

        # Create server-specific game data
        self.start_events[guild_id] = asyncio.Event()
        crash_point = self.generate_crash_point()
        self.server_games[guild_id] = CrashGame(self.bot, crash_point, guild_id)
        self.server_games[guild_id].add_player(interaction.user.id, bet)

        self.server_views[guild_id] = CrashView(self, self.server_games[guild_id])
        embed = await self.server_views[guild_id].create_embed()
        chart_file = await self.server_views[guild_id].create_chart()

        await interaction.response.send_message(embed=embed, view=self.server_views[guild_id], file=chart_file)
        self.server_messages[guild_id] = await interaction.original_response()

        self.logger.info(f"{interaction.user}가 {bet} 코인으로 크래시 게임 시작 (Guild: {guild_id})")
        await self.announce_crash_point(guild_id, crash_point)

        # Start the game lifecycle task for this server
        asyncio.create_task(self.game_lifecycle_task(guild_id))


async def setup(bot):
    await bot.add_cog(CrashCog(bot))